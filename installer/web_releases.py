"""Closed source identities; a recognized release is not permission to upgrade."""
from dataclasses import dataclass
from installer import mobile_web_source as mobile


@dataclass(frozen=True)
class WebRelease:
    commit: str
    tree: str
    files: int
    runtime_sha256: str
    external_uploads: bool


LEGACY_COMMIT = '46c03060625d4d53c675474b11aaa33007d9aad7'
STORAGE_COMMIT = '2a27c7a1f9fe0a00289eb53278f75d5f230900b7'
_RELEASES = (
    WebRelease(LEGACY_COMMIT, 'aaac278270e0fd1169396945916dfe997ae078bf', 1840,
               'b2205c6f7b326b0692e57942f282260669e3ec147daf3dc9f18c546782c7eb0f', False),
    WebRelease(STORAGE_COMMIT, '783be5abdcd5e13addefe96d743eee3a97b7a6de', 1843,
               '42c99a13f41b50a5263c69d14557dd088b5787b40ffdc51873b8d61ea1bc8edb', True),
    WebRelease(mobile.COMMIT, mobile.TREE, mobile.FILES, mobile.RUNTIME_SHA256, True),
)


def get_release(commit: str) -> WebRelease:
    for release in _RELEASES:
        if type(commit) is str and release.commit == commit:
            return release
    raise ValueError('SOURCE_PIN_MISMATCH')
