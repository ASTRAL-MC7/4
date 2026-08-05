"""
Wraps the Face++ (faceplusplus.com) API — replaces our old self-hosted
face model. Face++ does detection, storage, and matching for us and
gives back a ready confidence score, so there's no local threshold
tuning needed.

Get your free API_KEY / API_SECRET at https://console.faceplusplus.com
"""
import os
import asyncio
import cv2
import httpx

API_KEY = os.environ["FACEPP_API_KEY"]
API_SECRET = os.environ["FACEPP_API_SECRET"]
BASE_URL = os.environ.get("FACEPP_BASE_URL", "https://api-us.faceplusplus.com")

# All enrolled employees live in one Face++ "FaceSet" identified by this
# fixed name. Face++ auto-resolves outer_id -> faceset for every call,
# so we never need to store a faceset_token ourselves.
FACESET_OUTER_ID = "employee_faceset"

_faceset_ready = False


async def _post(client, path, data, files=None, retries=4):
    last_error = None
    for attempt in range(retries):
        resp = await client.post(
            f"{BASE_URL}{path}",
            data={"api_key": API_KEY, "api_secret": API_SECRET, **data},
            files=files,
            timeout=30,
        )
        try:
            result = resp.json()
        except Exception:
            raise RuntimeError(
                f"Face++ returned a non-JSON response on {path} "
                f"(status {resp.status_code}): {resp.text[:200]}"
            )

        error = result.get("error_message")
        if not error:
            return result

        last_error = error
        if "CONCURRENCY_LIMIT_EXCEEDED" in error and attempt < retries - 1:
            # Free-tier shared queue is briefly busy — back off and retry.
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        raise RuntimeError(f"Face++ error on {path}: {error}")

    raise RuntimeError(f"Face++ error on {path} after {retries} attempts: {last_error}")


async def ensure_faceset_exists():
    """Creates the shared FaceSet once. Safe to call every startup."""
    global _faceset_ready
    if _faceset_ready:
        return
    async with httpx.AsyncClient() as client:
        try:
            await _post(client, "/facepp/v3/faceset/create", {
                "outer_id": FACESET_OUTER_ID,
                "display_name": "Employees",
            })
        except RuntimeError as e:
            if "FACESET_EXIST" not in str(e) and "outer_id" not in str(e).lower():
                raise
            # already exists — fine, carry on
    _faceset_ready = True


async def enroll_face(image_bytes, name):
    """
    Detects the face in a photo, adds it to the shared FaceSet, and
    returns the face_token (which we store in our own DB linked to
    the employee's name).
    """
    async with httpx.AsyncClient() as client:
        detect_result = await _post(
            client, "/facepp/v3/detect", {},
            files={"image_file": ("photo.jpg", image_bytes, "image/jpeg")},
        )
        faces = detect_result.get("faces", [])
        if not faces:
            return None
        face_token = faces[0]["face_token"]

        await _post(client, "/facepp/v3/faceset/addface", {
            "outer_id": FACESET_OUTER_ID,
            "face_tokens": face_token,
        })
        return face_token


async def remove_face(face_token):
    """Removes a specific face from the shared FaceSet on Face++'s side."""
    async with httpx.AsyncClient() as client:
        await _post(client, "/facepp/v3/faceset/removeface", {
            "outer_id": FACESET_OUTER_ID,
            "face_tokens": face_token,
        })


async def search_face(image_bytes, result_count=5):
    """
    Searches one face image against the shared FaceSet.
    Returns (list_of_(face_token, confidence), thresholds).
    We return multiple candidates (not just the top one) because
    orphaned/duplicate enrollments in the FaceSet can occasionally
    outrank the "real" saved entry — the caller tries each in order
    until one has a matching name in our own database.
    """
    async with httpx.AsyncClient() as client:
        result = await _post(
            client, "/facepp/v3/search", {
                "outer_id": FACESET_OUTER_ID,
                "return_result_count": result_count,
            },
            files={"image_file": ("frame.jpg", image_bytes, "image/jpeg")},
        )
        results = result.get("results", [])
        thresholds = result.get("thresholds", {})
        candidates = [(r["face_token"], r["confidence"]) for r in results]
        return candidates, thresholds


def extract_best_frame_as_jpeg(video_path, max_frames=8, frame_skip=3):
    """
    Pulls a reasonably clear frame out of the circular video note and
    encodes it as JPEG bytes, ready to send to Face++. We don't do any
    local face detection here anymore — Face++ handles that.
    """
    cap = cv2.VideoCapture(video_path)
    frame_idx = 0
    checked = 0
    best_frame = None

    while checked < max_frames:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % frame_skip == 0:
            best_frame = frame  # keep the most recent sampled frame
            checked += 1
        frame_idx += 1

    cap.release()
    if best_frame is None:
        return None
    ok, buf = cv2.imencode(".jpg", best_frame)
    return buf.tobytes() if ok else None
