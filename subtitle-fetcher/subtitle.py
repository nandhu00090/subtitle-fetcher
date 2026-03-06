#!/usr/bin/env python3
"""CLI tool to search and fetch English subtitles from OpenSubtitles."""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import zipfile
from typing import Any

import requests

API_BASE_URL = "https://api.opensubtitles.com/api/v1"
REQUEST_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 90
USER_AGENT = "subtitle-fetcher/1.0"


class SubtitleFetcherError(Exception):
    """Raised when fetching subtitle fails with a user-facing error."""


def sanitize_movie_name(movie_name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9]+", "_", movie_name.strip()).strip("_").lower()
    return sanitized or "movie"


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_session() -> requests.Session:
    api_key = os.getenv("OPENSUBTITLES_API_KEY")
    if not api_key:
        raise SubtitleFetcherError(
            "Missing OpenSubtitles API key. "
            "Set OPENSUBTITLES_API_KEY environment variable first."
        )

    session = requests.Session()
    session.headers.update(
        {
            "Api-Key": api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    return session


def search_english_subtitles(session: requests.Session, movie_name: str) -> list[dict[str, Any]]:
    search_url = f"{API_BASE_URL}/subtitles"
    params = {
        "query": movie_name,
        "languages": "en",
        "order_by": "download_count",
        "order_direction": "desc",
    }

    try:
        response = session.get(search_url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise SubtitleFetcherError(f"Could not reach OpenSubtitles search API: {exc}") from exc

    if response.status_code == 401:
        raise SubtitleFetcherError("Unauthorized: check your OPENSUBTITLES_API_KEY value.")
    if response.status_code == 429:
        raise SubtitleFetcherError("Rate limit exceeded by OpenSubtitles. Try again later.")
    if response.status_code >= 400:
        raise SubtitleFetcherError(
            f"OpenSubtitles search failed ({response.status_code}): {response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise SubtitleFetcherError("OpenSubtitles returned invalid JSON for search.") from exc

    subtitles = payload.get("data", [])
    if not subtitles:
        raise SubtitleFetcherError(f'No English subtitles found for "{movie_name}".')

    return subtitles


def _subtitle_score(item: dict[str, Any]) -> tuple[float, int, int, int]:
    attributes = item.get("attributes", {})
    return (
        _safe_float(attributes.get("ratings")),
        _safe_int(attributes.get("download_count")),
        1 if attributes.get("from_trusted") else 0,
        1 if not attributes.get("machine_translated") else 0,
    )


def build_subtitle_candidates(subtitles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for item in subtitles:
        attributes = item.get("attributes", {})
        files = attributes.get("files") or []
        if not files:
            continue

        file_id = files[0].get("file_id")
        if not isinstance(file_id, int):
            continue

        release_name = (
            attributes.get("release")
            or attributes.get("feature_details", {}).get("title")
            or "Unknown release"
        )
        candidate = {
            "file_id": file_id,
            "release": str(release_name),
            "rating": _safe_float(attributes.get("ratings")),
            "downloads": _safe_int(attributes.get("download_count")),
            "trusted": bool(attributes.get("from_trusted")),
            "machine_translated": bool(attributes.get("machine_translated")),
            "score": _subtitle_score(item),
        }
        candidates.append(candidate)

    if not candidates:
        raise SubtitleFetcherError("No downloadable subtitle files were found in search results.")

    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    return candidates


def show_subtitle_results(candidates: list[dict[str, Any]]) -> None:
    print("\nTop subtitle results:")
    for index, candidate in enumerate(candidates, start=1):
        release = candidate["release"]
        rating = candidate["rating"]
        downloads = candidate["downloads"]
        trusted = "yes" if candidate["trusted"] else "no"
        machine = "yes" if candidate["machine_translated"] else "no"
        print(
            f"{index:>2}. {release} "
            f"(rating: {rating:.1f}, downloads: {downloads}, trusted: {trusted}, machine_translated: {machine})"
        )
    print("")


def choose_subtitle(
    candidates: list[dict[str, Any]],
    auto_select: bool,
) -> dict[str, Any]:
    if auto_select or len(candidates) == 1:
        return candidates[0]

    if not sys.stdin.isatty():
        print("Non-interactive input detected. Automatically choosing the top result.")
        return candidates[0]

    prompt = (
        f"Select subtitle [1-{len(candidates)}], Enter for 1, or q to quit: "
    )
    while True:
        try:
            selection = input(prompt).strip().lower()
        except EOFError as exc:
            raise SubtitleFetcherError("No selection provided in non-interactive input.") from exc

        if selection in {"", "1"}:
            return candidates[0]
        if selection in {"q", "quit", "exit"}:
            raise SubtitleFetcherError("Selection cancelled by user.")
        if not selection.isdigit():
            print("Invalid selection. Enter a number or q to quit.")
            continue

        index = int(selection)
        if 1 <= index <= len(candidates):
            return candidates[index - 1]

        print(f"Selection out of range. Choose 1-{len(candidates)}.")


def request_download_link(session: requests.Session, file_id: int) -> tuple[str, str]:
    download_url = f"{API_BASE_URL}/download"
    payload = {"file_id": file_id, "sub_format": "srt"}

    try:
        response = session.post(download_url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise SubtitleFetcherError(f"Could not request subtitle download link: {exc}") from exc

    if response.status_code == 401:
        raise SubtitleFetcherError("Unauthorized while requesting download link.")
    if response.status_code == 429:
        raise SubtitleFetcherError("Rate limit exceeded when requesting download link.")
    if response.status_code >= 400:
        raise SubtitleFetcherError(
            f"OpenSubtitles download-link request failed ({response.status_code}): {response.text[:200]}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise SubtitleFetcherError("OpenSubtitles returned invalid JSON for download link.") from exc

    link = data.get("link")
    filename = data.get("file_name") or "subtitle.srt"
    if not link:
        raise SubtitleFetcherError("OpenSubtitles response did not include a download link.")
    return str(link), str(filename)


def download_subtitle_bytes(download_link: str) -> bytes:
    try:
        response = requests.get(download_link, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise SubtitleFetcherError(f"Failed to download subtitle file: {exc}") from exc

    if response.status_code >= 400:
        raise SubtitleFetcherError(f"Subtitle download failed ({response.status_code}).")

    content = response.content
    if not content:
        raise SubtitleFetcherError("Downloaded subtitle file was empty.")
    return content


def extract_srt_if_zip(content: bytes) -> bytes:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return content

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        srt_names = [name for name in archive.namelist() if name.lower().endswith(".srt")]
        if not srt_names:
            raise SubtitleFetcherError("ZIP archive did not contain any .srt files.")

        # Prefer the first .srt entry in the archive.
        with archive.open(srt_names[0]) as subtitle_file:
            extracted = subtitle_file.read()
            if not extracted:
                raise SubtitleFetcherError("Extracted .srt file from ZIP was empty.")
            return extracted


def save_subtitle_file(movie_name: str, subtitle_content: bytes) -> str:
    output_filename = f"{sanitize_movie_name(movie_name)}.en.srt"
    output_path = os.path.abspath(output_filename)
    try:
        with open(output_path, "wb") as file:
            file.write(subtitle_content)
    except OSError as exc:
        raise SubtitleFetcherError(f"Could not save subtitle file: {exc}") from exc

    return output_path


def fetch_and_save_subtitle(movie_name: str, auto_select: bool, max_results: int) -> str:
    session = build_session()
    subtitles = search_english_subtitles(session, movie_name)
    candidates = build_subtitle_candidates(subtitles)
    top_candidates = candidates[:max_results]
    show_subtitle_results(top_candidates)
    selected = choose_subtitle(top_candidates, auto_select=auto_select)
    file_id = selected["file_id"]
    release_name = selected["release"]

    print(f"Selected subtitle release: {release_name}")
    download_link, _filename = request_download_link(session, file_id)
    print("Downloading subtitle file...")
    downloaded_content = download_subtitle_bytes(download_link)
    subtitle_content = extract_srt_if_zip(downloaded_content)
    return save_subtitle_file(movie_name, subtitle_content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search OpenSubtitles, choose a subtitle, and save it as .srt."
    )
    parser.add_argument("movie_name", help='Movie title to search, e.g. "Inception"')
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Automatically choose the top result without prompting.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="Number of top results to show (default: 5).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.max_results < 1:
            raise SubtitleFetcherError("--max-results must be at least 1.")
        output_path = fetch_and_save_subtitle(
            args.movie_name,
            auto_select=args.auto,
            max_results=args.max_results,
        )
    except SubtitleFetcherError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: cancelled by user.", file=sys.stderr)
        return 130

    print(f"Subtitle saved to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
