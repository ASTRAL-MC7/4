import os
import asyncpg

DATABASE_URL = os.environ["DATABASE_URL"]


async def get_pool():
    # Neon requires SSL
    return await asyncpg.create_pool(DATABASE_URL, ssl="require", min_size=1, max_size=5)


async def init_db(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS people (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
        # Migration safety: older versions of this bot used different
        # columns. Add what we need now, and remove old leftover
        # columns/constraints that would otherwise block inserts.
        await conn.execute(
            "ALTER TABLE people ADD COLUMN IF NOT EXISTS face_token TEXT"
        )
        await conn.execute(
            "ALTER TABLE people ADD COLUMN IF NOT EXISTS tg_id BIGINT"
        )
        await conn.execute(
            "ALTER TABLE people DROP COLUMN IF EXISTS embedding"
        )


async def save_person(pool, name, face_token, tg_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO people (name, face_token, tg_id) VALUES ($1, $2, $3)",
            name, face_token, tg_id,
        )


async def get_name_by_face_token(pool, face_token):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name FROM people WHERE face_token = $1", face_token
        )
        return row["name"] if row else None


async def is_tg_id_enrolled(pool, tg_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM people WHERE tg_id = $1", tg_id
        )
        return row is not None


async def get_all_people(pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, tg_id, face_token FROM people ORDER BY name ASC"
        )
        return [dict(r) for r in rows]


async def get_person_by_id(pool, person_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, tg_id, face_token FROM people WHERE id = $1", person_id
        )
        return dict(row) if row else None


async def delete_person_by_id(pool, person_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM people WHERE id = $1", person_id)


async def rename_person(pool, person_id, new_name):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE people SET name = $1 WHERE id = $2", new_name, person_id
        )


async def update_person_face(pool, person_id, new_face_token):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE people SET face_token = $1 WHERE id = $2", new_face_token, person_id
        )


# Kept for backward compatibility with the old /remove-by-name flow.
async def delete_person(pool, name):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM people WHERE name = $1", name)
