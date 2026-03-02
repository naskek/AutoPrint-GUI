# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from typing import Iterable

try:
    import psycopg
except Exception:
    psycopg = None

from utils import get_base_dir, load_env_file


_ENV_LOADED = False


def _load_env_once() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = os.path.join(get_base_dir(), ".env")
    load_env_file(env_path, override=False)
    _ENV_LOADED = True


def db_enabled() -> bool:
    _load_env_once()
    value = str(os.environ.get("AUTOPRINT_DB_ENABLED", "") or "").strip().lower()
    return value in ("1", "true", "yes", "on")


def get_db_url() -> str:
    _load_env_once()
    return str(os.environ.get("AUTOPRINT_DB_URL", "") or "").strip()


def get_conn():
    if psycopg is None:
        raise RuntimeError('Модуль psycopg не установлен. Установите: pip install "psycopg[binary]"')
    db_url = get_db_url()
    if not db_url:
        raise RuntimeError("Не задан AUTOPRINT_DB_URL в .env")
    return psycopg.connect(db_url)


def init_db() -> None:
    ddl = [
        """
        create table if not exists imports (
            id bigserial primary key,
            source_filename text not null,
            created_at timestamptz not null default now()
        )
        """,
        """
        create table if not exists import_rows (
            id bigserial primary key,
            import_id bigint not null references imports(id) on delete cascade,
            row_no integer not null,
            dm text not null,
            gtin text,
            name text,
            created_at timestamptz not null default now()
        )
        """,
        """
        create table if not exists print_jobs (
            id bigserial primary key,
            import_id bigint references imports(id) on delete set null,
            source_filename text,
            source_file_hash text,
            source_file_size bigint,
            csv_path text,
            batch_index integer,
            batch_start_row integer,
            batch_end_row integer,
            total_rows integer,
            rows_in_batch integer,
            calibration_rows integer,
            printer text,
            format text,
            template_path text,
            status text not null default 'created',
            error_text text,
            user_name text,
            created_at timestamptz not null default now(),
            started_at timestamptz,
            finished_at timestamptz,
            duration_ms integer
        )
        """,
        """
        create table if not exists print_job_rows (
            id bigserial primary key,
            print_job_id bigint not null references print_jobs(id) on delete cascade,
            row_no_in_job integer not null,
            source_row_no integer,
            dm text,
            gtin text,
            name text,
            part_num text,
            prod_date text,
            exp_date text,
            format text,
            short_gtin text,
            short_name text,
            is_calibration boolean not null default false,
            payload_json text
        )
        """,
        """
        create table if not exists codes (
            id bigserial primary key,
            dm text not null unique,
            gtin text,
            name text,
            part_num text,
            prod_date text,
            exp_date text,
            first_seen_import_id bigint references imports(id) on delete set null,
            last_seen_import_id bigint references imports(id) on delete set null,
            first_seen_at timestamptz not null default now(),
            last_seen_at timestamptz not null default now(),
            import_count integer not null default 1,
            print_success_count integer not null default 0,
            last_print_status text,
            last_print_job_id bigint references print_jobs(id) on delete set null,
            last_printed_at timestamptz,
            crpt_status text,
            crpt_checked_at timestamptz
        )
        """,
        "create index if not exists idx_import_rows_import_id on import_rows(import_id)",
        "create index if not exists idx_import_rows_gtin on import_rows(gtin)",
        "create index if not exists idx_print_jobs_import_id on print_jobs(import_id)",
        "create index if not exists idx_print_jobs_created_at on print_jobs(created_at)",
        "create index if not exists idx_print_jobs_status on print_jobs(status)",
        "create index if not exists idx_print_job_rows_job_id on print_job_rows(print_job_id)",
        "create index if not exists idx_print_job_rows_dm on print_job_rows(dm)",
        "create index if not exists idx_codes_gtin on codes(gtin)",
        "create index if not exists idx_codes_last_seen_at on codes(last_seen_at)",
        "create index if not exists idx_codes_last_print_status on codes(last_print_status)",
        "create index if not exists idx_codes_crpt_status on codes(crpt_status)",
    ]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in ddl:
                cur.execute(stmt)
            # Метаданные импорта (PoC -> расширение схемы).
            cur.execute("alter table imports add column if not exists file_hash text")
            cur.execute("alter table imports add column if not exists file_size bigint")
            cur.execute("alter table imports add column if not exists row_count_total integer")
            cur.execute("alter table imports add column if not exists row_count_inserted integer")
            cur.execute("alter table imports add column if not exists row_count_duplicates integer")
            cur.execute("create index if not exists idx_imports_file_hash on imports(file_hash)")
            # Мягкая миграция: если print_jobs создан ранее без новых полей.
            cur.execute("alter table print_jobs add column if not exists source_file_hash text")
            cur.execute("alter table print_jobs add column if not exists source_file_size bigint")
            cur.execute("alter table print_jobs add column if not exists csv_path text")
            cur.execute("alter table print_jobs add column if not exists duration_ms integer")
            cur.execute("create index if not exists idx_print_jobs_source_file_hash on print_jobs(source_file_hash)")
            # Мягкая миграция таблицы codes (если таблица уже создана ранней PoC-версией).
            cur.execute("alter table codes add column if not exists part_num text")
            cur.execute("alter table codes add column if not exists prod_date text")
            cur.execute("alter table codes add column if not exists exp_date text")
            cur.execute("alter table codes add column if not exists first_seen_import_id bigint references imports(id) on delete set null")
            cur.execute("alter table codes add column if not exists last_seen_import_id bigint references imports(id) on delete set null")
            cur.execute("alter table codes add column if not exists first_seen_at timestamptz not null default now()")
            cur.execute("alter table codes add column if not exists last_seen_at timestamptz not null default now()")
            cur.execute("alter table codes add column if not exists import_count integer not null default 1")
            cur.execute("alter table codes add column if not exists print_success_count integer not null default 0")
            cur.execute("alter table codes add column if not exists last_print_status text")
            cur.execute("alter table codes add column if not exists last_print_job_id bigint references print_jobs(id) on delete set null")
            cur.execute("alter table codes add column if not exists last_printed_at timestamptz")
            cur.execute("alter table codes add column if not exists crpt_status text")
            cur.execute("alter table codes add column if not exists crpt_checked_at timestamptz")
            # Миграция к глобальной уникальности DM:
            # если в таблице уже есть дубли, оставляем самую раннюю запись.
            cur.execute(
                """
                with ranked as (
                    select
                        id,
                        row_number() over (partition by dm order by id asc) as rn
                    from import_rows
                )
                delete from import_rows r
                using ranked x
                where r.id = x.id
                  and x.rn > 1
                """
            )
            cur.execute("drop index if exists idx_import_rows_dm")
            cur.execute("create unique index if not exists idx_import_rows_dm_unique on import_rows(dm)")
            # Убираем пустые импорты (например, созданные ранней версией PoC).
            cur.execute(
                """
                delete from imports i
                where not exists (
                    select 1 from import_rows r where r.import_id = i.id
                )
                """
            )
            # Бэкфилл статистики для уже существующих импортов (старые PoC версии).
            cur.execute(
                """
                update imports i
                set
                    row_count_total = coalesce(i.row_count_total, x.cnt),
                    row_count_inserted = coalesce(i.row_count_inserted, x.cnt),
                    row_count_duplicates = coalesce(i.row_count_duplicates, 0)
                from (
                    select import_id, count(*)::int as cnt
                    from import_rows
                    group by import_id
                ) x
                where x.import_id = i.id
                """
            )
            # Бэкфилл канонической таблицы codes из уже существующих import_rows.
            cur.execute(
                """
                insert into codes (dm, gtin, name, first_seen_import_id, last_seen_import_id)
                select
                    s.dm,
                    s.gtin,
                    s.name,
                    s.import_id,
                    s.import_id
                from (
                    select distinct on (r.dm)
                        r.dm,
                        coalesce(r.gtin, '') as gtin,
                        coalesce(r.name, '') as name,
                        r.import_id
                    from import_rows r
                    where coalesce(r.dm, '') <> ''
                    order by r.dm, r.id desc
                ) s
                on conflict (dm) do nothing
                """
            )
            # Бэкфилл последнего статуса печати и счетчика успешных печатей по уже накопленной истории.
            cur.execute(
                """
                with last_job as (
                    select distinct on (r.dm)
                        r.dm,
                        j.id as print_job_id,
                        j.status,
                        coalesce(j.finished_at, j.created_at) as job_ts
                    from print_job_rows r
                    join print_jobs j on j.id = r.print_job_id
                    where coalesce(r.dm, '') <> ''
                      and not coalesce(r.is_calibration, false)
                    order by r.dm, j.id desc
                ),
                success_counts as (
                    select
                        r.dm,
                        count(distinct r.print_job_id)::int as cnt
                    from print_job_rows r
                    join print_jobs j on j.id = r.print_job_id
                    where coalesce(r.dm, '') <> ''
                      and not coalesce(r.is_calibration, false)
                      and j.status = 'done'
                    group by r.dm
                )
                update codes c
                set
                    last_print_status = coalesce(l.status, c.last_print_status),
                    last_print_job_id = coalesce(l.print_job_id, c.last_print_job_id),
                    last_printed_at = case
                        when coalesce(l.status, '') = 'done' then coalesce(l.job_ts, c.last_printed_at)
                        else c.last_printed_at
                    end,
                    print_success_count = coalesce(s.cnt, c.print_success_count)
                from last_job l
                left join success_counts s on s.dm = l.dm
                where c.dm = l.dm
                """
            )
        conn.commit()


def save_import(
    rows: Iterable[dict],
    source_filename: str,
    file_hash: str | None = None,
    file_size: int | None = None,
) -> tuple[int | None, int, int]:
    rows_list = list(rows or [])
    if not rows_list:
        return None, 0, 0

    prepared: list[tuple[int, str, str, str]] = []
    for idx, row in enumerate(rows_list, start=1):
        item = row or {}
        dm = str(item.get("DM", "") or "").strip()
        gtin = str(item.get("GTIN", "") or "").strip()
        name = str(item.get("NAME", "") or "").strip()
        prepared.append((idx, dm, gtin, name))

    # Ищем уже существующие DM в БД заранее, чтобы не создавать пустой imports-запись,
    # если весь файл оказался дублем.
    with get_conn() as conn:
        with conn.cursor() as cur:
            existing_dm: set[str] = set()
            dm_values = [dm for (_idx, dm, _gtin, _name) in prepared if dm]
            chunk_size = 1000
            for i in range(0, len(dm_values), chunk_size):
                chunk = dm_values[i:i + chunk_size]
                if not chunk:
                    continue
                cur.execute(
                    "select dm from import_rows where dm = any(%s)",
                    (chunk,),
                )
                existing_dm.update(str(row[0] or "") for row in cur.fetchall())

            payload: list[tuple[int, int, str, str, str]] = []
            seen_in_file: set[str] = set()
            for row_no, dm, gtin, name in prepared:
                if not dm:
                    continue
                if dm in seen_in_file:
                    continue
                seen_in_file.add(dm)
                if dm in existing_dm:
                    continue
                payload.append((0, row_no, dm, gtin, name))

            if not payload:
                conn.commit()
                return None, 0, len(rows_list)

            cur.execute(
                """
                insert into imports (
                    source_filename,
                    file_hash,
                    file_size,
                    row_count_total,
                    row_count_inserted,
                    row_count_duplicates
                )
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    str(source_filename or "").strip() or "unknown.csv",
                    (str(file_hash or "").strip() or None),
                    int(file_size) if file_size is not None else None,
                    int(len(rows_list)),
                    0,
                    0,
                ),
            )
            import_id = int(cur.fetchone()[0])

            payload = [(import_id, row_no, dm, gtin, name) for (_zero, row_no, dm, gtin, name) in payload]
            cur.executemany(
                """
                insert into import_rows (import_id, row_no, dm, gtin, name)
                values (%s, %s, %s, %s, %s)
                on conflict (dm) do nothing
                """,
                payload,
            )
            cur.execute("select count(*) from import_rows where import_id = %s", (import_id,))
            inserted_count = int(cur.fetchone()[0])
            duplicate_count = max(0, len(rows_list) - inserted_count)
            # Обновляем канонический реестр кодов (включая частично повторяющиеся файлы):
            # по каждому уникальному DM в файле обновляем last_seen и известные поля.
            codes_payload: list[tuple[str, str, str, int, int]] = []
            seen_for_codes: set[str] = set()
            for _row_no, dm, gtin, name in prepared:
                if not dm or dm in seen_for_codes:
                    continue
                seen_for_codes.add(dm)
                codes_payload.append((dm, gtin, name, import_id, import_id))
            if codes_payload:
                cur.executemany(
                    """
                    insert into codes (
                        dm,
                        gtin,
                        name,
                        first_seen_import_id,
                        last_seen_import_id
                    )
                    values (%s, %s, %s, %s, %s)
                    on conflict (dm) do update
                    set
                        gtin = case
                            when coalesce(excluded.gtin, '') <> '' then excluded.gtin
                            else codes.gtin
                        end,
                        name = case
                            when coalesce(excluded.name, '') <> '' then excluded.name
                            else codes.name
                        end,
                        last_seen_import_id = excluded.last_seen_import_id,
                        last_seen_at = now(),
                        import_count = greatest(1, coalesce(codes.import_count, 1) + 1)
                    """,
                    codes_payload,
                )
            cur.execute(
                """
                update imports
                set
                    row_count_total = %s,
                    row_count_inserted = %s,
                    row_count_duplicates = %s
                where id = %s
                """,
                (int(len(rows_list)), inserted_count, duplicate_count, import_id),
            )
            # Теоретически возможна гонка между precheck и insert (другой процесс вставил те же DM).
            # В этом случае удаляем пустой imports и считаем всё дублями.
            if inserted_count <= 0:
                cur.execute("delete from imports where id = %s", (import_id,))
                conn.commit()
                return None, 0, len(rows_list)
        conn.commit()
    return import_id, inserted_count, duplicate_count


def create_print_job(
    *,
    import_id: int | None,
    source_filename: str | None,
    source_file_hash: str | None = None,
    source_file_size: int | None = None,
    csv_path: str | None = None,
    batch_index: int | None = None,
    batch_start_row: int | None = None,
    batch_end_row: int | None = None,
    total_rows: int | None = None,
    rows_in_batch: int | None = None,
    calibration_rows: int | None = None,
    printer: str | None = None,
    format_name: str | None = None,
    template_path: str | None = None,
    user_name: str | None = None,
    status: str = "created",
) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into print_jobs (
                    import_id,
                    source_filename,
                    source_file_hash,
                    source_file_size,
                    csv_path,
                    batch_index,
                    batch_start_row,
                    batch_end_row,
                    total_rows,
                    rows_in_batch,
                    calibration_rows,
                    printer,
                    format,
                    template_path,
                    status,
                    user_name,
                    started_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                returning id
                """,
                (
                    int(import_id) if import_id else None,
                    str(source_filename or "") or None,
                    str(source_file_hash or "") or None,
                    int(source_file_size) if source_file_size is not None else None,
                    str(csv_path or "") or None,
                    int(batch_index) if batch_index is not None else None,
                    int(batch_start_row) if batch_start_row is not None else None,
                    int(batch_end_row) if batch_end_row is not None else None,
                    int(total_rows) if total_rows is not None else None,
                    int(rows_in_batch) if rows_in_batch is not None else None,
                    int(calibration_rows) if calibration_rows is not None else None,
                    str(printer or "") or None,
                    str(format_name or "") or None,
                    str(template_path or "") or None,
                    str(status or "created"),
                    str(user_name or "") or None,
                ),
            )
            print_job_id = int(cur.fetchone()[0])
        conn.commit()
    return print_job_id


def add_print_job_rows(
    print_job_id: int,
    rows: Iterable[dict],
    *,
    start_row_no: int | None = None,
) -> int:
    rows_list = list(rows or [])
    payload = []
    for idx, row in enumerate(rows_list, start=1):
        item = row or {}
        source_row_no = None
        try:
            explicit_source_row = item.get("_SOURCE_ROW_NO")
        except Exception:
            explicit_source_row = None
        if explicit_source_row is not None:
            try:
                source_row_no = int(explicit_source_row)
            except Exception:
                source_row_no = None
        elif start_row_no is not None:
            try:
                source_row_no = int(start_row_no) + (idx - 1)
            except Exception:
                source_row_no = None
        try:
            is_calib = bool(item.get("_CALIB"))
        except Exception:
            is_calib = False
        # JSON payload keeps the full row for future replay/debug while columns support quick filtering.
        try:
            payload_json = json.dumps(item, ensure_ascii=False)
        except Exception:
            payload_json = None
        payload.append(
            (
                int(print_job_id),
                idx,
                source_row_no,
                str(item.get("DM", "") or ""),
                str(item.get("GTIN", "") or ""),
                str(item.get("NAME", "") or ""),
                str(item.get("PART_NUM", "") or ""),
                str(item.get("PROD_DATE", "") or ""),
                str(item.get("EXP_DATE", "") or ""),
                str(item.get("_FORMAT", "") or ""),
                str(item.get("ShortGTIN", "") or ""),
                str(item.get("ShortName", "") or ""),
                is_calib,
                payload_json,
            )
        )
    if not payload:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                insert into print_job_rows (
                    print_job_id,
                    row_no_in_job,
                    source_row_no,
                    dm,
                    gtin,
                    name,
                    part_num,
                    prod_date,
                    exp_date,
                    format,
                    short_gtin,
                    short_name,
                    is_calibration,
                    payload_json
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                payload,
            )
        conn.commit()
    return len(payload)


def update_print_job(
    print_job_id: int,
    *,
    status: str | None = None,
    error_text: str | None = None,
    finished: bool = False,
    duration_ms: int | None = None,
    printer: str | None = None,
    format_name: str | None = None,
    template_path: str | None = None,
) -> None:
    updates = []
    params: list[object] = []
    if status is not None:
        updates.append("status = %s")
        params.append(str(status))
    if error_text is not None:
        updates.append("error_text = %s")
        params.append(str(error_text) if error_text else None)
    if duration_ms is not None:
        updates.append("duration_ms = %s")
        params.append(int(duration_ms))
    if printer is not None:
        updates.append("printer = %s")
        params.append(str(printer) if printer else None)
    if format_name is not None:
        updates.append("format = %s")
        params.append(str(format_name) if format_name else None)
    if template_path is not None:
        updates.append("template_path = %s")
        params.append(str(template_path) if template_path else None)
    if finished:
        updates.append("finished_at = now()")
    if not updates:
        return
    params.append(int(print_job_id))
    sql = f"update print_jobs set {', '.join(updates)} where id = %s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            # Синхронизируем агрегированный статус кодов по строкам этого job.
            if status is not None:
                status_value = str(status or "").strip()
                cur.execute(
                    """
                    with job_codes as (
                        select distinct on (r.dm)
                            r.dm,
                            coalesce(r.gtin, '') as gtin,
                            coalesce(r.name, '') as name,
                            coalesce(r.part_num, '') as part_num,
                            coalesce(r.prod_date, '') as prod_date,
                            coalesce(r.exp_date, '') as exp_date
                        from print_job_rows r
                        where r.print_job_id = %s
                          and coalesce(r.dm, '') <> ''
                          and not coalesce(r.is_calibration, false)
                        order by r.dm, r.id desc
                    )
                    insert into codes (dm, gtin, name, part_num, prod_date, exp_date)
                    select dm, gtin, name, part_num, prod_date, exp_date
                    from job_codes
                    on conflict (dm) do update
                    set
                        gtin = case when coalesce(excluded.gtin, '') <> '' then excluded.gtin else codes.gtin end,
                        name = case when coalesce(excluded.name, '') <> '' then excluded.name else codes.name end,
                        part_num = case when coalesce(excluded.part_num, '') <> '' then excluded.part_num else codes.part_num end,
                        prod_date = case when coalesce(excluded.prod_date, '') <> '' then excluded.prod_date else codes.prod_date end,
                        exp_date = case when coalesce(excluded.exp_date, '') <> '' then excluded.exp_date else codes.exp_date end
                    """,
                    (int(print_job_id),),
                )
                cur.execute(
                    """
                    with job_codes as (
                        select distinct on (r.dm)
                            r.dm,
                            coalesce(r.part_num, '') as part_num,
                            coalesce(r.prod_date, '') as prod_date,
                            coalesce(r.exp_date, '') as exp_date
                        from print_job_rows r
                        where r.print_job_id = %s
                          and coalesce(r.dm, '') <> ''
                          and not coalesce(r.is_calibration, false)
                        order by r.dm, r.id desc
                    )
                    update codes c
                    set
                        last_print_status = %s,
                        last_print_job_id = %s,
                        last_printed_at = case
                            when %s = 'done' then now()
                            else c.last_printed_at
                        end,
                        print_success_count = case
                            when %s = 'done' then coalesce(c.print_success_count, 0) + 1
                            else coalesce(c.print_success_count, 0)
                        end,
                        part_num = case when coalesce(job_codes.part_num, '') <> '' then job_codes.part_num else c.part_num end,
                        prod_date = case when coalesce(job_codes.prod_date, '') <> '' then job_codes.prod_date else c.prod_date end,
                        exp_date = case when coalesce(job_codes.exp_date, '') <> '' then job_codes.exp_date else c.exp_date end
                    from job_codes
                    where c.dm = job_codes.dm
                    """,
                    (
                        int(print_job_id),
                        status_value,
                        int(print_job_id),
                        status_value,
                        status_value,
                    ),
                )
        conn.commit()


def count_rows_for_import(import_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) from import_rows where import_id = %s", (int(import_id),))
            return int(cur.fetchone()[0])


def _dt_to_text(value) -> str:
    if value is None:
        return ""
    try:
        return value.isoformat(timespec="seconds")
    except Exception:
        return str(value)


def list_recent_imports(limit: int = 20) -> list[dict]:
    limit = max(1, int(limit or 20))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    i.id,
                    i.source_filename,
                    i.created_at,
                    coalesce(i.file_hash, '') as file_hash,
                    i.file_size,
                    coalesce(i.row_count_total, count(r.id)::int) as row_count_total,
                    coalesce(i.row_count_inserted, count(r.id)::int) as row_count_inserted,
                    coalesce(i.row_count_duplicates, 0) as row_count_duplicates
                from imports i
                left join import_rows r on r.import_id = i.id
                group by
                    i.id, i.source_filename, i.created_at,
                    i.file_hash, i.file_size,
                    i.row_count_total, i.row_count_inserted, i.row_count_duplicates
                order by i.id desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": int(row[0]),
                "source_filename": str(row[1] or ""),
                "created_at": _dt_to_text(row[2]),
                "file_hash": str(row[3] or ""),
                "file_size": None if row[4] is None else int(row[4]),
                "row_count_total": int(row[5] or 0),
                "row_count_inserted": int(row[6] or 0),
                "row_count_duplicates": int(row[7] or 0),
                # backward compatibility for old GUI code
                "row_count": int(row[6] or 0),
            }
        )
    return out


def find_import_by_file_hash(file_hash: str) -> dict | None:
    hash_value = str(file_hash or "").strip()
    if not hash_value:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    i.id,
                    i.source_filename,
                    i.created_at,
                    coalesce(i.file_hash, '') as file_hash,
                    i.file_size,
                    coalesce(i.row_count_total, count(r.id)::int) as row_count_total,
                    coalesce(i.row_count_inserted, count(r.id)::int) as row_count_inserted,
                    coalesce(i.row_count_duplicates, 0) as row_count_duplicates
                from imports i
                left join import_rows r on r.import_id = i.id
                where i.file_hash = %s
                group by
                    i.id, i.source_filename, i.created_at,
                    i.file_hash, i.file_size,
                    i.row_count_total, i.row_count_inserted, i.row_count_duplicates
                order by i.id desc
                limit 1
                """,
                (hash_value,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": int(row[0]),
        "source_filename": str(row[1] or ""),
        "created_at": _dt_to_text(row[2]),
        "file_hash": str(row[3] or ""),
        "file_size": None if row[4] is None else int(row[4]),
        "row_count_total": int(row[5] or 0),
        "row_count_inserted": int(row[6] or 0),
        "row_count_duplicates": int(row[7] or 0),
        "row_count": int(row[6] or 0),
    }


def get_import_rows(import_id: int, limit: int = 500, offset: int = 0) -> list[dict]:
    limit = max(1, int(limit or 500))
    offset = max(0, int(offset or 0))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    id,
                    row_no,
                    dm,
                    gtin,
                    name,
                    created_at
                from import_rows
                where import_id = %s
                order by row_no asc, id asc
                limit %s offset %s
                """,
                (int(import_id), limit, offset),
            )
            rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": int(row[0]),
                "row_no": int(row[1] or 0),
                "dm": str(row[2] or ""),
                "gtin": str(row[3] or ""),
                "name": str(row[4] or ""),
                "created_at": _dt_to_text(row[5]),
            }
        )
    return out


def search_import_rows(query: str, limit: int = 100) -> list[dict]:
    text = str(query or "").strip()
    if not text:
        return []
    limit = max(1, int(limit or 100))
    pattern = f"%{text}%"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    r.import_id,
                    i.source_filename,
                    i.created_at,
                    r.row_no,
                    r.dm,
                    r.gtin,
                    r.name
                from import_rows r
                join imports i on i.id = r.import_id
                where
                    r.dm ilike %s
                    or coalesce(r.gtin, '') ilike %s
                    or coalesce(r.name, '') ilike %s
                order by r.import_id desc, r.row_no asc
                limit %s
                """,
                (pattern, pattern, pattern, limit),
            )
            rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "import_id": int(row[0]),
                "source_filename": str(row[1] or ""),
                "import_created_at": _dt_to_text(row[2]),
                "row_no": int(row[3] or 0),
                "dm": str(row[4] or ""),
                "gtin": str(row[5] or ""),
                "name": str(row[6] or ""),
            }
        )
    return out


def list_recent_print_jobs(limit: int = 100, status: str | None = None) -> list[dict]:
    limit = max(1, int(limit or 100))
    status_value = str(status or "").strip()
    params: list[object] = []
    where_sql = ""
    if status_value and status_value.lower() not in ("all", "*"):
        where_sql = "where j.status = %s"
        params.append(status_value)
    params.append(limit)
    sql = f"""
        select
            j.id,
            j.import_id,
            j.source_filename,
            coalesce(j.source_file_hash, '') as source_file_hash,
            j.source_file_size,
            j.csv_path,
            j.batch_index,
            j.batch_start_row,
            j.batch_end_row,
            j.total_rows,
            j.rows_in_batch,
            j.calibration_rows,
            j.printer,
            j.format,
            j.template_path,
            j.status,
            coalesce(j.error_text, '') as error_text,
            j.user_name,
            j.created_at,
            j.started_at,
            j.finished_at,
            j.duration_ms,
            count(r.id)::int as saved_rows,
            coalesce(sum(case when r.is_calibration then 1 else 0 end), 0)::int as saved_calibration_rows
        from print_jobs j
        left join print_job_rows r on r.print_job_id = j.id
        {where_sql}
        group by
            j.id, j.import_id, j.source_filename, j.source_file_hash, j.source_file_size, j.csv_path,
            j.batch_index, j.batch_start_row, j.batch_end_row, j.total_rows, j.rows_in_batch, j.calibration_rows,
            j.printer, j.format, j.template_path, j.status, j.error_text, j.user_name,
            j.created_at, j.started_at, j.finished_at, j.duration_ms
        order by j.id desc
        limit %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": int(row[0]),
                "import_id": None if row[1] is None else int(row[1]),
                "source_filename": str(row[2] or ""),
                "source_file_hash": str(row[3] or ""),
                "source_file_size": None if row[4] is None else int(row[4]),
                "csv_path": str(row[5] or ""),
                "batch_index": None if row[6] is None else int(row[6]),
                "batch_start_row": None if row[7] is None else int(row[7]),
                "batch_end_row": None if row[8] is None else int(row[8]),
                "total_rows": None if row[9] is None else int(row[9]),
                "rows_in_batch": None if row[10] is None else int(row[10]),
                "calibration_rows": None if row[11] is None else int(row[11]),
                "printer": str(row[12] or ""),
                "format": str(row[13] or ""),
                "template_path": str(row[14] or ""),
                "status": str(row[15] or ""),
                "error_text": str(row[16] or ""),
                "user_name": str(row[17] or ""),
                "created_at": _dt_to_text(row[18]),
                "started_at": _dt_to_text(row[19]),
                "finished_at": _dt_to_text(row[20]),
                "duration_ms": None if row[21] is None else int(row[21]),
                "saved_rows": int(row[22] or 0),
                "saved_calibration_rows": int(row[23] or 0),
            }
        )
    return out


def get_print_job_rows(print_job_id: int, limit: int = 2000, offset: int = 0) -> list[dict]:
    limit = max(1, int(limit or 2000))
    offset = max(0, int(offset or 0))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    id,
                    row_no_in_job,
                    source_row_no,
                    dm,
                    gtin,
                    name,
                    part_num,
                    prod_date,
                    exp_date,
                    format,
                    short_gtin,
                    short_name,
                    is_calibration,
                    payload_json
                from print_job_rows
                where print_job_id = %s
                order by row_no_in_job asc, id asc
                limit %s offset %s
                """,
                (int(print_job_id), limit, offset),
            )
            rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": int(row[0]),
                "row_no_in_job": int(row[1] or 0),
                "source_row_no": None if row[2] is None else int(row[2]),
                "dm": str(row[3] or ""),
                "gtin": str(row[4] or ""),
                "name": str(row[5] or ""),
                "part_num": str(row[6] or ""),
                "prod_date": str(row[7] or ""),
                "exp_date": str(row[8] or ""),
                "format": str(row[9] or ""),
                "short_gtin": str(row[10] or ""),
                "short_name": str(row[11] or ""),
                "is_calibration": bool(row[12]),
                "payload_json": str(row[13] or ""),
            }
        )
    return out


def list_recent_codes(limit: int = 200, print_status: str | None = None) -> list[dict]:
    limit = max(1, int(limit or 200))
    status_value = str(print_status or "").strip()
    params: list[object] = []
    where_sql = ""
    if status_value and status_value.lower() not in ("all", "*", "any", "Все"):
        if status_value.lower() == "never_printed":
            where_sql = "where coalesce(c.print_success_count, 0) = 0"
        else:
            where_sql = "where coalesce(c.last_print_status, '') = %s"
            params.append(status_value)
    params.append(limit)
    sql = f"""
        select
            c.id,
            c.dm,
            coalesce(c.gtin, '') as gtin,
            coalesce(c.name, '') as name,
            coalesce(c.part_num, '') as part_num,
            coalesce(c.prod_date, '') as prod_date,
            coalesce(c.exp_date, '') as exp_date,
            c.first_seen_import_id,
            c.last_seen_import_id,
            c.first_seen_at,
            c.last_seen_at,
            c.import_count,
            c.print_success_count,
            coalesce(c.last_print_status, '') as last_print_status,
            c.last_print_job_id,
            c.last_printed_at,
            coalesce(c.crpt_status, '') as crpt_status,
            c.crpt_checked_at
        from codes c
        {where_sql}
        order by c.last_seen_at desc, c.id desc
        limit %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": int(row[0]),
                "dm": str(row[1] or ""),
                "gtin": str(row[2] or ""),
                "name": str(row[3] or ""),
                "part_num": str(row[4] or ""),
                "prod_date": str(row[5] or ""),
                "exp_date": str(row[6] or ""),
                "first_seen_import_id": None if row[7] is None else int(row[7]),
                "last_seen_import_id": None if row[8] is None else int(row[8]),
                "first_seen_at": _dt_to_text(row[9]),
                "last_seen_at": _dt_to_text(row[10]),
                "import_count": int(row[11] or 0),
                "print_success_count": int(row[12] or 0),
                "last_print_status": str(row[13] or ""),
                "last_print_job_id": None if row[14] is None else int(row[14]),
                "last_printed_at": _dt_to_text(row[15]),
                "crpt_status": str(row[16] or ""),
                "crpt_checked_at": _dt_to_text(row[17]),
            }
        )
    return out


def search_codes(query: str, limit: int = 200, print_status: str | None = None) -> list[dict]:
    text = str(query or "").strip()
    if not text:
        return []
    limit = max(1, int(limit or 200))
    status_value = str(print_status or "").strip()
    pattern = f"%{text}%"
    prefix = f"{text}%"
    status_params: list[object] = []
    status_sql = ""
    if status_value and status_value.lower() not in ("all", "*", "any", "Все"):
        if status_value.lower() == "never_printed":
            status_sql = "and coalesce(c.print_success_count, 0) = 0"
        else:
            status_sql = "and coalesce(c.last_print_status, '') = %s"
            status_params.append(status_value)
    where_params: list[object] = [text, text, prefix, prefix, pattern, pattern, pattern]
    order_params: list[object] = [text, text, prefix, prefix]
    sql_params = tuple(where_params + status_params + order_params + [limit])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    c.id,
                    c.dm,
                    coalesce(c.gtin, '') as gtin,
                    coalesce(c.name, '') as name,
                    coalesce(c.part_num, '') as part_num,
                    coalesce(c.prod_date, '') as prod_date,
                    coalesce(c.exp_date, '') as exp_date,
                    c.first_seen_import_id,
                    c.last_seen_import_id,
                    c.first_seen_at,
                    c.last_seen_at,
                    c.import_count,
                    c.print_success_count,
                    coalesce(c.last_print_status, '') as last_print_status,
                    c.last_print_job_id,
                    c.last_printed_at,
                    coalesce(c.crpt_status, '') as crpt_status,
                    c.crpt_checked_at
                from codes c
                where (
                    c.dm = %s
                    or coalesce(c.gtin, '') = %s
                    or c.dm ilike %s
                    or coalesce(c.gtin, '') ilike %s
                    or c.dm ilike %s
                    or coalesce(c.gtin, '') ilike %s
                    or coalesce(c.name, '') ilike %s
                )
                {status_sql}
                order by
                    case
                        when c.dm = %s then 0
                        when coalesce(c.gtin, '') = %s then 1
                        when c.dm ilike %s then 2
                        when coalesce(c.gtin, '') ilike %s then 3
                        else 4
                    end,
                    c.last_seen_at desc,
                    c.id desc
                limit %s
                """,
                sql_params,
            )
            rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": int(row[0]),
                "dm": str(row[1] or ""),
                "gtin": str(row[2] or ""),
                "name": str(row[3] or ""),
                "part_num": str(row[4] or ""),
                "prod_date": str(row[5] or ""),
                "exp_date": str(row[6] or ""),
                "first_seen_import_id": None if row[7] is None else int(row[7]),
                "last_seen_import_id": None if row[8] is None else int(row[8]),
                "first_seen_at": _dt_to_text(row[9]),
                "last_seen_at": _dt_to_text(row[10]),
                "import_count": int(row[11] or 0),
                "print_success_count": int(row[12] or 0),
                "last_print_status": str(row[13] or ""),
                "last_print_job_id": None if row[14] is None else int(row[14]),
                "last_printed_at": _dt_to_text(row[15]),
                "crpt_status": str(row[16] or ""),
                "crpt_checked_at": _dt_to_text(row[17]),
            }
        )
    return out
