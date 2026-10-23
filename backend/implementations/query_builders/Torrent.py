# -*- coding: utf-8 -*-

"""
Query builder for torrent indexers. Torrent sites match search terms as loose
tokens against release names (e.g. "Batman 001 (2016) (Digital)"), so the
queries are kept short and without punctuation like "#" and "()".
"""

from backend.base.definitions import (DownloadType, QueryBuilder,
                                      QueryKeys, SearchAction, SearchQuery,
                                      SpecialVersion)
from backend.implementations.query_builder_manager import QueryBuilders

TPB_FORMATS = (
    "{title} Vol {volume_number} {year}",
    "{title} v{volume_number_padded}",
    "{title} TPB",
    "{title}"
)

VAI_FORMATS = (
    "{title} {year}",
    "{title}"
)

VOLUME_FORMATS = (
    "{title} {year}",
    "{title}"
)

SPECIFIC_ISSUE_FORMATS = (
    "{title} {issue_number_padded} {year}",
    "{title} {issue_number_padded}",
    "{title} {issue_number}"
)

GENERAL_ISSUE_FORMATS = SPECIFIC_ISSUE_FORMATS + ("{title}",)


def pad_number(number: str, width: int) -> str:
    """Zero-pad a number the way releases do (e.g. "1" -> "001"). Non-integer
    numbers (e.g. "1.5" or "½") are left untouched.

    Args:
        number (str): The number to pad.
        width (int): The width to pad to.

    Returns:
        str: The padded number.
    """
    if number.isdigit():
        return number.zfill(width)
    return number


@QueryBuilders.register_builder(DownloadType.TORRENT)
class TorrentQueryBuilder(QueryBuilder):
    def next_query(
        self,
        search_action: SearchAction,
        query_keys: QueryKeys
    ) -> SearchQuery:
        super()._update_state(search_action)

        if query_keys.special_version == SpecialVersion.TPB:
            queries = TPB_FORMATS

        elif query_keys.special_version == SpecialVersion.VOLUME_AS_ISSUE:
            queries = VAI_FORMATS

        elif query_keys.issue_number is None:
            queries = VOLUME_FORMATS

        elif self.originally_volume_search:
            queries = SPECIFIC_ISSUE_FORMATS

        else:
            queries = GENERAL_ISSUE_FORMATS

        query = queries[self.query_variation_index]

        if query_keys.year is None:
            query = query.replace('{year}', '')

        issue_number = query_keys.issue_number or ''
        result = query.format(
            title=query_keys.titles[self.alias_index],
            year=query_keys.year,
            volume_number=query_keys.volume_number,
            volume_number_padded=pad_number(
                str(query_keys.volume_number), 2
            ),
            issue_number=issue_number,
            issue_number_padded=pad_number(issue_number, 3)
        )

        return {
            "query": ' '.join(result.split()),
            "page": self.page,
            "total_available_variations": len(queries)
        }
