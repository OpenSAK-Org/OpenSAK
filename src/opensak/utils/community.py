"""
src/opensak/utils/community.py — where the OpenSAK community is (#1230).

The links shown under Help → Community. They are kept here, in one place,
so a moved Facebook group or forum thread only needs one change.
"""

from __future__ import annotations

# The group's permanent URL, not a share link: non-members can still see
# the group page and ask to join.
FACEBOOK_GROUP_URL = "https://www.facebook.com/groups/opensak"
DISCUSSIONS_URL = "https://github.com/OpenSAK-Org/OpenSAK/discussions"
# OpenSAK's own section of the GSAK forum, under "Not strictly GSAK, but
# related": the pinned announcements and the users' own topics.
GSAK_FORUM_URL = "https://forum.gsak.net/index.php?showforum=52"
# "new/choose" shows the bug report and feature request templates.
NEW_ISSUE_URL = "https://github.com/OpenSAK-Org/OpenSAK/issues/new/choose"

# (translation key, URL) in the order they are shown in the menu.
COMMUNITY_LINKS: tuple[tuple[str, str], ...] = (
    ("action_community_facebook", FACEBOOK_GROUP_URL),
    ("action_community_discussions", DISCUSSIONS_URL),
    ("action_community_gsak_forum", GSAK_FORUM_URL),
    ("action_community_new_issue", NEW_ISSUE_URL),
)
