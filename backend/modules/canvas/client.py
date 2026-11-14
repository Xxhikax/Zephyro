"""Canvas LMS REST API client.

Reads CANVAS_BASE_URL and CANVAS_API_TOKEN from environment.
All methods are async and use httpx.AsyncClient.
Pagination via Link header is handled transparently by _get_all().
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import httpx

# params type: dict OR list of (key, value) tuples (needed for repeated keys)
_Params = Optional[Union[dict, list]]


def _parse_next_link(link_header: str) -> Optional[str]:
    """Extract the 'next' URL from a Canvas Link header."""
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            m = re.search(r"<([^>]+)>", part)
            if m:
                return m.group(1)
    return None


def _short_course_name(full_name: str) -> str:
    """Extract the readable part of a Canvas course name (before the first '|')."""
    return full_name.split("|")[0].strip()


class CanvasClient:
    """Thin async wrapper around the Canvas LMS REST API v1."""

    TIMEOUT = 15.0

    def __init__(self) -> None:
        self.base = os.getenv("CANVAS_BASE_URL", "").rstrip("/")
        self.token = os.getenv("CANVAS_API_TOKEN", "")
        self._headers = {"Authorization": f"Bearer {self.token}"}

    def is_configured(self) -> bool:
        return bool(self.base and self.token)

    async def _get_all(self, path: str, params: _Params = None) -> list:
        """GET a paginated Canvas endpoint, following Link headers automatically."""
        url = f"{self.base}/api/v1/{path}"
        results: list = []
        first = True
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            while url:
                r = await client.get(
                    url,
                    headers=self._headers,
                    params=params if first else None,
                )
                r.raise_for_status()
                data = r.json()
                if isinstance(data, list):
                    results.extend(data)
                else:
                    results.append(data)
                url = _parse_next_link(r.headers.get("Link", ""))
                first = False
        return results

    # ── Public methods ────────────────────────────────────────────

    async def get_courses(self) -> list:
        """Return active courses for the current user."""
        return await self._get_all(
            "courses",
            {"enrollment_state": "active", "per_page": 50},
        )

    async def get_upcoming_assignments(self, days: int = 7) -> list:
        """Return assignments due within the next `days` days, sorted by due date.

        Scans each course's assignment list because /upcoming_events may be
        empty on some Canvas configurations.
        """
        now = datetime.now(tz=timezone.utc)
        cutoff = now + timedelta(days=days)
        courses = await self.get_courses()

        assignments = []
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            for course in courses:
                cid = course.get("id")
                cname = _short_course_name(course.get("name", ""))
                if not cid:
                    continue
                r = await client.get(
                    f"{self.base}/api/v1/courses/{cid}/assignments",
                    headers=self._headers,
                    params={"order_by": "due_at", "per_page": 30},
                )
                if r.status_code != 200:
                    continue
                for a in r.json():
                    due_str = a.get("due_at")
                    if not due_str:
                        continue
                    try:
                        due = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if now <= due <= cutoff:
                        assignments.append({
                            "id": a.get("id", ""),
                            "title": a.get("name", "Sin título"),
                            "course": cname,
                            "due_at": due,
                            "url": a.get("html_url", ""),
                        })

        assignments.sort(key=lambda x: x["due_at"])
        return assignments

    async def get_missing_submissions(self) -> list:
        """Return submissions marked as missing/late by Canvas."""
        raw = await self._get_all(
            "users/self/missing_submissions",
            {"per_page": 50},
        )
        # Build course id → name map to display readable names
        courses = await self.get_courses()
        course_map = {str(c["id"]): _short_course_name(c.get("name", "")) for c in courses}

        result = []
        for item in raw:
            due_str = item.get("due_at")
            due = None
            if due_str:
                try:
                    due = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                except ValueError:
                    pass
            lock_at = None
            lock_str = item.get("lock_at")
            if lock_str:
                try:
                    lock_at = datetime.fromisoformat(lock_str.replace("Z", "+00:00"))
                except ValueError:
                    pass
            result.append({
                "id": item.get("id", item.get("assignment_id", "")),
                "title": item.get("name", "Sin título"),
                "course": course_map.get(str(item.get("course_id", "")), ""),
                "due_at": due,
                "lock_at": lock_at,
                "url": item.get("html_url", ""),
                "points_possible": item.get("points_possible"),
            })
        return result

    async def get_course_grades(self) -> list:
        """Return current grade per active course."""
        courses = await self._get_all(
            "courses",
            [
                ("enrollment_state", "active"),
                ("include[]", "total_scores"),
                ("per_page", "50"),
            ],
        )
        grades = []
        for course in courses:
            name = _short_course_name(course.get("name", "Desconocido"))
            # Canvas devuelve el enrollment DUPLICADO en varios cursos (verificado
            # 2026-10-01: 6 de 9 cursos traen dos entradas 'student'). Quedarse con
            # el primero a ciegas deja la nota del curso a merced del orden; se
            # prefiere el que tenga score.
            enrollments = sorted(
                course.get("enrollments", []),
                key=lambda e: e.get("computed_current_score") is None,
            )
            for enr in enrollments:
                if enr.get("type") == "student":
                    # Se incluye el curso AUNQUE no tenga nota (score=None).
                    # Filtrarlo acá hacía que un curso sin calificar desapareciera
                    # de la lista, y quien preguntara por él recibía "no encontré
                    # ese curso" en vez de "todavía no tiene nota" — dos cosas
                    # muy distintas para el usuario (bug real, 2026-09-30).
                    grades.append({
                        "course": name,
                        "course_id": course.get("id"),
                        "grade": enr.get("computed_current_grade"),
                        "score": enr.get("computed_current_score"),
                    })
                    break
        return grades

    async def get_announcements(self, limit: int = 5) -> list:
        """Return the most recent announcements from all active courses."""
        courses = await self.get_courses()
        course_ids = [str(c["id"]) for c in courses if "id" in c]
        if not course_ids:
            return []

        # httpx requires a list of tuples to send the same key multiple times
        params: list = [("per_page", "20")]
        for cid in course_ids:
            params.append(("context_codes[]", f"course_{cid}"))

        raw = await self._get_all("announcements", params)
        raw.sort(key=lambda x: x.get("posted_at", ""), reverse=True)
        result = []
        for item in raw[:limit]:
            result.append({
                "title": item.get("title", "Sin título"),
                "course": item.get("context_name", ""),
                "posted_at": item.get("posted_at"),
                "message": _strip_html(item.get("message", ""))[:200],
            })
        return result

    async def get_assignments_with_scores(self, course_id: int) -> list:
        """Return assignments with individual submission scores for a course."""
        raw = await self._get_all(
            f"courses/{course_id}/assignments",
            [("include[]", "submission"), ("per_page", "100")],
        )
        result = []
        for a in raw:
            sub = a.get("submission") or {}
            result.append({
                "title": a.get("name", ""),
                "score": sub.get("score"),
                "points_possible": a.get("points_possible"),
                "graded": sub.get("workflow_state") == "graded",
            })
        return result

    async def get_today_calendar_events(self) -> list:
        """Return calendar events scheduled for today."""
        today = datetime.now(tz=timezone.utc).date()
        raw = await self._get_all(
            "calendar_events",
            {
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
                "per_page": 50,
            },
        )
        result = []
        for item in raw:
            result.append({
                "title": item.get("title", "Sin título"),
                "start": item.get("start_at"),
                "end": item.get("end_at"),
                "context": item.get("context_name", ""),
            })
        return result


def _strip_html(text: str) -> str:
    """Remove basic HTML tags from announcement body."""
    return re.sub(r"<[^>]+>", " ", text).strip()
