"""Event fields, default names, and source failures."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from gallery.core.locales._catalog import Message

MESSAGES: Final[Mapping[str, Message]] = {
    "event.kind.trip.empty": "Untitled trip",
    "event.kind.show.empty": "Untitled show",
    "event.kind.movie.empty": "Untitled movie",
    "event.kind.trip": "Trip",
    "event.kind.show": "Show",
    "event.kind.movie": "Movie",
    "event.field.cover": "Cover",
    "event.field.title": "Name",
    "event.field.title.hint": "Leave it blank to build one from the date and key fields",
    "event.field.started_on": "Start date",
    "event.field.started_on.hint": "2026-05-16; a year and month is enough",
    "event.field.ended_on": "End date",
    "event.field.ended_on.hint": "Same as the start date for a day trip",
    "event.field.note": "Note",
    "event.field.destination": "Destination",
    "event.field.destination.hint": "Separate multiple places with commas",
    "event.field.companions": "Companions",
    "event.field.companions.hint": "Separate with commas",
    "event.field.transport": "Transport",
    "event.field.lodging": "Lodging",
    "event.field.cost": "Cost",
    "event.field.cost.hint": "Numbers only; leave it blank to skip",
    "event.field.rating": "Rating",
    "event.field.artist": "Artist",
    "event.field.artist.hint": "Separate multiple names with commas",
    "event.field.guests": "Guests",
    "event.field.guests.hint": "Separate multiple names with commas",
    "event.field.program": "Program",
    "event.field.program.hint": "Name of the play or musical",
    "event.field.show_type": "Show type",
    "event.field.show_type.hint": "Concert, musical, play, ...",
    "event.field.start_time": "Start time",
    "event.field.city": "City",
    "event.field.venue": "Venue",
    "event.field.venue.hint": "For example, the National Centre for the Performing Arts",
    "event.field.ticket_price": "Ticket price",
    "event.field.ticket_price.hint": "Face value; numbers only",
    "event.field.paid": "Paid",
    "event.field.paid.hint": "Amount actually paid; numbers only",
    "event.field.seat": "Seat",
    "event.field.seat.hint": "For example, Section A, row 12, seat 5",
    "event.field.channel": "Purchase channel",
    "event.field.channel.hint": "For example, Damai or Showstart",
    "event.field.thoughts": "Thoughts",
    "event.field.source": "Source",
    "event.field.code": "Code",
    "event.field.code.hint": (
        "A JavDB code, a YouTube videoId, a Bilibili BV number, or a Tencent / iQiyi / "
        "Youku / Mango TV / Douyin / TikTok / Pornhub code"
    ),
    "event.field.duration": "Duration",
    "event.field.duration.hint": "Minutes; numbers only",
    "event.field.creator": "Creator",
    "event.field.creator.hint": "Studio, channel, or uploader",
    "event.field.label": "Publisher",
    "event.field.series": "Series",
    "event.field.series.hint": "Series or collection",
    "event.field.director": "Director",
    "event.field.actors": "Actors",
    "event.field.actors.hint": "Separate multiple names with commas",
    "event.field.tags": "Tags",
    "event.field.tags.hint": "Separate multiple tags with commas",
    "event.suggested_title.new": "New {kind}",
    "event.problem.title": (
        "This {kind} needs a name: it is the only way to recognize this one later."
    ),
    "event.problem.started_on": (
        "Could not understand the start date “{date}”. Use 2026-05-16, or just 2026-05."
    ),
    "event.problem.ended_on": (
        "Could not understand the end date “{date}”. Use 2026-05-20, or just 2026-05."
    ),
    "event.problem.ended_before_started": (
        "The end date ({end}) is earlier than the start date ({start})."
    ),
    "event.problem.cost_not_number": (
        "{label} is not a valid number: “{text}”. Enter numbers only, such as 3280 or 3280.50."
    ),
    "event.problem.cost_negative": "{label} cannot be negative.",
    "event.error.unknown_kind": "Unrecognized event kind: {kind}",
    "event.error.not_found": "This event no longer exists.",
    "event.error.undo_unreadable": "Could not read this event record, so it cannot be undone.",
    "event.error.undo_unknown_kind": "Unrecognized event kind: {kind}.",
    "event.error.undo_id_taken": (
        "The library already has an event with id {event_id}; refusing to overwrite it."
    ),
    "event.error.cover_not_bound": "Choose the cover from this event's photos.",
    "event.error.event_missing": "There is no event with id {event_id}",
    "event.error.events_unavailable": (
        "The event library is unavailable: this library cannot create its configuration folder."
    ),
    "event.orphan.unnamed": "Untitled",
    "event.import_shows.not_show_export": (
        "The spreadsheet has no “演出名称” column, so it may not be a show list exported by “演迹”."
    ),
    "event.import_shows.skipped_activity": "Row {row} is an activity rather than a show; skipped.",
    "event.import_shows.skipped_no_title": "Row {row} has no show name; skipped.",
    "event.import_shows.file_missing": "Could not find this file: {name}.",
    "event.import_shows.not_xlsx": "“{name}” is not an .xlsx spreadsheet exported by “演迹”.",
    "event.transport.unknown_reason": "unknown reason",
    "event.transport.unreachable": "Could not reach the source site; try again later: {reason}.",
    "event.transport.webpage_not_image": ("The downloaded cover is a web page, not an image."),
    "event.movie.empty_input": "The code or link cannot be empty.",
    "event.movie.unrecognized_url": (
        "Could not recognize this address: {url}. For a short link, open it in the browser "
        "first and paste the full address; for a collection page, open a single episode first "
        "and copy that."
    ),
    "event.movie.unknown_source": "Unrecognized source: {source}.",
    "event.movie.no_cover": "{code} has no cover image.",
    "event.jav.not_found": "The source site does not have this code: {code}.",
    "event.jav.multiple": (
        "Found several close matches for code {code}; cannot tell which one it is."
    ),
    "event.jav.no_results": (
        "The source site does not have this code: {code}. Check the spelling, or try another code."
    ),
    "event.jav.search_blocked": (
        "The source site returned no search results: {code}. This is usually rate limiting or "
        "restricted access; try again later."
    ),
    "event.jav.empty_code": "The code cannot be empty.",
    "event.jav.no_cover": "No cover image was found on the page for code {code}.",
    "event.youtube.embed_forbidden": (
        "YouTube does not provide this video's details and does not allow embedded playback: "
        "{code}. Open it in the browser to check."
    ),
    "event.youtube.not_found": (
        "YouTube does not have this video: {code}. It may have been deleted or made private."
    ),
    "event.youtube.http": "YouTube did not provide details this time: HTTP {status}, {code}.",
    "event.youtube.invalid_code": "Not a valid YouTube videoId: {code}.",
    "event.youtube.unparsable": "Could not parse the information returned by YouTube: {code}.",
    "event.youtube.invalid_payload": (
        "The information returned by YouTube is not valid video information: {code}."
    ),
    "event.bilibili.problem.invalid_code": "Bilibili returned: invalid code",
    "event.bilibili.problem.forbidden": "Bilibili will not show this video; permission denied",
    "event.bilibili.problem.not_found": "Bilibili does not have this video",
    "event.bilibili.problem.risk_control": (
        "Bilibili's risk control did not let this through; try again later, or open it in the "
        "browser first"
    ),
    "event.bilibili.problem.unavailable": (
        "This video is not visible; it may have been deleted or made private"
    ),
    "event.bilibili.problem.under_review": ("This video is still under review; try again later"),
    "event.bilibili.problem.up_only": "Only the uploader can see this video",
    "event.bilibili.problem_known": "{reason}: {code}.",
    "event.bilibili.problem_unknown": (
        "Bilibili did not provide this video's details: error code {status}."
    ),
    "event.bilibili.problem_unknown_message": (
        "Bilibili did not provide this video's details: error code {status}, {message}."
    ),
    "event.bilibili.no_info": "Bilibili did not provide this video's details: {code}.",
    "event.bilibili.unparsable": "Could not parse the information returned by Bilibili: {code}.",
    "event.bilibili.invalid_payload": (
        "The information returned by Bilibili is not valid video information: {code}."
    ),
    "event.bilibili.invalid_code": "Not a valid BV number: {code}.",
    "event.douyin.invalid_code": "Not a valid Douyin video code or share link: {code}.",
    "event.douyin.share_expand_failed": (
        "Could not expand this Douyin share link: {code}. Open it in the browser first, then "
        "paste the full address."
    ),
    "event.douyin.share_resolve_failed": (
        "Could not resolve a video code from this Douyin share link: {code}. Open it in the "
        "browser first, then paste the full address."
    ),
    "event.douyin.unparsable": "Could not parse the information returned by Douyin: {code}.",
    "event.douyin.invalid_payload": (
        "The information returned by Douyin is not valid video information: {code}."
    ),
    "event.douyin.not_found": "Douyin does not have this video: {code}.",
    "event.douyin.no_info": (
        "Douyin did not provide this video's details: {code}. The video may have been deleted "
        "or made private, or the site's page structure may have changed; open it in the browser "
        "to check."
    ),
    "event.douyin.no_title": "Douyin did not provide this video's title: {code}.",
    "event.iqiyi.problem": "iQiyi did not provide this video's details: {status} ({code}).",
    "event.iqiyi.problem_message": (
        "iQiyi did not provide this video's details: {status}, {message} ({code})."
    ),
    "event.iqiyi.no_info": "iQiyi did not provide this video's details: {code}.",
    "event.iqiyi.invalid_code": "Not a valid iQiyi code: {code}.",
    "event.iqiyi.not_found": "iQiyi does not have this video: {code}.",
    "event.iqiyi.no_tvid": (
        "This iQiyi page did not provide a video code: {code}. The video may have been taken "
        "down, or the address may not be a single episode or movie."
    ),
    "event.iqiyi.unparsable": "Could not parse the information returned by iQiyi.",
    "event.iqiyi.invalid_payload": (
        "The information returned by iQiyi is not valid video information."
    ),
    "event.tencent.problem.not_found": "Tencent Video does not have this video, or it is offline",
    "event.tencent.problem.under_review": (
        "Tencent Video says this video is still under review; try again later"
    ),
    "event.tencent.problem_known": "{reason}: {code}.",
    "event.tencent.problem_unknown": (
        "Tencent Video did not provide this video's details: error code {status}."
    ),
    "event.tencent.problem_unknown_message": (
        "Tencent Video did not provide this video's details: error code {status}, {message}."
    ),
    "event.tencent.no_info": "Tencent Video did not provide this video's details: {code}.",
    "event.tencent.unparsable": ("Could not parse the information returned by Tencent Video."),
    "event.tencent.invalid_payload": (
        "The information returned by Tencent Video is not valid video information."
    ),
    "event.tencent.invalid_code": "Not a valid Tencent Video vid: {code}.",
    "event.youku.no_info_reason": (
        "This Youku page did not provide movie details: {code}. The site may have changed its "
        "page structure; open it in the browser to check."
    ),
    "event.youku.no_info": "This Youku page did not provide movie details: {code}.",
    "event.youku.invalid_code": "Not a valid Youku code: {code}.",
    "event.youku.risk_control": (
        "Youku's risk control did not let this through: {code}. Try again later, or open it in "
        "the browser first."
    ),
    "event.mango.problem.not_found": "Mango TV does not have this video",
    "event.mango.problem_known": "{reason}: {code}.",
    "event.mango.problem_unknown": (
        "Mango TV did not provide this video's details: error code {status}."
    ),
    "event.mango.problem_unknown_message": (
        "Mango TV did not provide this video's details: error code {status}, {message}."
    ),
    "event.mango.no_info": "Mango TV did not provide this video's details: {code}.",
    "event.mango.invalid_code": "Not a valid Mango TV vid: {code}.",
    "event.mango.unparsable": "Could not parse the information returned by Mango TV.",
    "event.mango.invalid_payload": (
        "The information returned by Mango TV is not valid video information."
    ),
    "event.tiktok.no_info_region": (
        "TikTok did not provide this video's details; it may have been deleted, made private, "
        "or be region-restricted: {code}."
    ),
    "event.tiktok.not_found": "TikTok does not have this video: {code}.",
    "event.tiktok.http": "TikTok did not provide details this time: HTTP {status}, {code}.",
    "event.tiktok.unparsable": "Could not parse the information returned by TikTok: {code}.",
    "event.tiktok.no_info": "TikTok did not provide this video's details: {code}.",
    "event.tiktok.invalid_code": "Not a valid TikTok video id or share link: {code}.",
    "event.tiktok.no_id": "TikTok did not provide this video's id: {code}.",
    "event.pornhub.no_info_reason": (
        "This Pornhub page did not provide movie details: {code}. The site may have changed its "
        "page structure; open it in the browser to check."
    ),
    "event.pornhub.geo_blocked": (
        "This video is not available from the current network location: {code}."
    ),
    "event.pornhub.unavailable": (
        "Pornhub does not have this video, or it has been deleted / made private: {code}."
    ),
    "event.pornhub.invalid_code": "Not a valid Pornhub code: {code}.",
    "event.pornhub.not_found": "Pornhub does not have this video: {code}.",
    "event.field.start_time.hint": "19:30",
    "event.kind.default_empty": "Untitled",
}
