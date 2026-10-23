# -*- coding: utf-8 -*-

from typing import List, Set, Tuple, Union

from backend.base.definitions import (IssueData, QueryKeys, SearchAction,
                                      SearchIterationStats, VolumeData)


class SearchActionPlanner:
    """
    The Search Action Planner controls what is done each iteration of the search
    cycle for an indexer and query builder. It acts based on the current state
    and the results of the previous search (i.e. a Mealy-type FSM).
    """

    def __init__(
        self,
        volume_data: VolumeData,
        issue_data: List[IssueData],
        wanted_issues: List[int],
        search_issues_without_volume_match: bool = False
    ) -> None:
        """Initialise the planner.

        Args:
            volume_data (VolumeData): The data of the volume that is going to
                be searched for.
            issue_data (List[IssueData]): The issue data of all issues in the
                volume.
            wanted_issues (List[int]): The issue IDs of the issues that we want
                search results for.
            search_issues_without_volume_match (bool, optional): Search for
                the individual issues, even if searching for the volume didn't
                give any matches. Useful for indexers where releases are mostly
                single issues (e.g. torrent sites), instead of pages with the
                whole volume (e.g. GetComics). The search then stops after
                multiple issues in a row couldn't be found.
                Defaults to False.
        """
        self.volume_data = volume_data
        self.issue_data = {
            issue.id: issue
            for issue in issue_data
        }
        self.wanted_issues = wanted_issues
        self.search_issues_without_volume_match = (
            search_issues_without_volume_match
        )

        self.using_alt_title = False
        self.max_variations = 1
        self.variation = 1
        self.sequential_failed_variations = 0
        self.sequential_failed_issue_searches = 0
        self.page = 1

        self.volume_phase = True
        self.found_a_match = False
        self.processed_issues: Set[int] = set()
        self.current_issue: Union[int, None] = None
        if len(wanted_issues) == 1:
            self.volume_phase = False
            self.current_issue = wanted_issues[0]

        self.titles = [self.volume_data.title]
        if self.volume_data.alt_title:
            self.titles.append(self.volume_data.alt_title)

        self.stats: Union[SearchIterationStats, None] = None

        return

    def process_stats(self, stats: SearchIterationStats) -> None:
        """Process statistics about how a search went.

        Args:
            stats (SearchIterationStats): The statistics.
        """
        self.stats = stats
        self.wanted_issues = stats.remaining_wanted_issues
        self.max_variations = stats.total_available_variations
        if stats.matched_count:
            self.found_a_match = True

        if not self.stats.matched_count and self.page == 1:
            # Not even a match on the first page
            self.sequential_failed_variations += 1

        if not self.volume_phase:
            self.wanted_issues = [
                i
                for i in self.wanted_issues
                if i not in self.processed_issues
            ]

        return

    def next_action(self) -> Tuple[SearchAction, QueryKeys]:
        """Get the next action to perform, based on the current state and how
        the previous search went.

        Returns:
            Tuple[SearchAction, QueryKeys]: The next action, and accompanying
                query keys for the query builder.
        """
        # Stop
        if len(self.wanted_issues) == 0:
            return (SearchAction.STOP, self._build_keys())

        if self.stats is None:
            # First iteration, start search
            if self.volume_phase:
                return (SearchAction.SEARCH_VOLUME, self._build_keys())
            else:
                return (SearchAction.SEARCH_ISSUE, self._build_keys())

        if (
            self.volume_phase
            and self.stats.matched_count
            and self.stats.next_page_available
        ):
            # Matches found and more pages available. Keep going.
            self.page += 1
            return (SearchAction.FETCH_NEXT_PAGE, self._build_keys())

        if (
            not self.volume_phase
            and self.stats.new_match_count
            and self.current_issue is not None # For type checking
        ):
            # Match found for issue in issue phase. Next issue.
            self.processed_issues.add(self.current_issue)
            next_issue = self._next_issue()
            if next_issue is None:
                return (SearchAction.STOP, self._build_keys())
            self.current_issue = next_issue
            self.page = 1
            self.variation = 1
            self.sequential_failed_variations = 0
            self.sequential_failed_issue_searches = 0
            return (SearchAction.SEARCH_ISSUE, self._build_keys())

        # No matches or no more pages left. Next variation.

        if self.variation == self.max_variations:
            # No variations left
            if (
                self.sequential_failed_variations == self.max_variations
                and not self.using_alt_title
                and len(self.titles) > 1
            ):
                # None of the variations yielded matches. Switch to alias title.
                self.page = 1
                self.variation = 1
                self.using_alt_title = True
                self.sequential_failed_variations = 0
                return (SearchAction.NEXT_TITLE_ALIAS, self._build_keys())

            if (
                not self.found_a_match
                and not self.search_issues_without_volume_match
            ):
                # No variations yielded matches, not even with alias title
                return (SearchAction.STOP, self._build_keys())

            if self.volume_phase:
                # No volume queries left but still wanted issues. Search for
                # each remaining issue individually.
                next_issue = self._next_issue()
                if next_issue is None:
                    return (SearchAction.STOP, self._build_keys())
                self.volume_phase = False
                self.current_issue = next_issue
                self.processed_issues.add(self.current_issue)
                self.page = 1
                self.variation = 1
                self.sequential_failed_variations = 0
                return (SearchAction.SEARCH_ISSUE, self._build_keys())

            elif self.wanted_issues and self.current_issue:
                # In issue phase, couldn't find issue
                if self.sequential_failed_issue_searches == 3:
                    # Three consecutive searches failed. We're probably
                    # not going to find any of them.
                    return (SearchAction.STOP, self._build_keys())

                # Continue to next one.
                self.processed_issues.add(self.current_issue)
                next_issue = self._next_issue()
                if next_issue is None:
                    return (SearchAction.STOP, self._build_keys())
                self.current_issue = next_issue
                self.page = 1
                self.variation = 1
                self.sequential_failed_variations = 0
                self.sequential_failed_issue_searches += 1
                return (SearchAction.SEARCH_ISSUE, self._build_keys())

            # In issue phase with no issues left. Should theoretically be
            # handled by guard at the very top of the method already.
            return (SearchAction.STOP, self._build_keys())

        # Next variation
        self.variation += 1
        self.page = 1
        return (SearchAction.NEXT_QUERY_VARIATION, self._build_keys())

    def _next_issue(self) -> Union[int, None]:
        """Get the next wanted issue that hasn't been searched for yet.

        Returns:
            Union[int, None]: The ID of the issue, or None if there are none.
        """
        return next(
            (
                i
                for i in self.wanted_issues
                if i not in self.processed_issues
            ),
            None
        )

    def _build_keys(self) -> QueryKeys:
        """Build the query keys to be supplied to the query builder, based on
        the current state of the planner.

        Returns:
            QueryKeys: The resulting keys to be used by the query builder.
        """
        issue_number = None
        if self.current_issue is not None:
            issue_number = self.issue_data[self.current_issue].issue_number

        return QueryKeys(
            titles=self.titles,
            year=self.volume_data.year,
            volume_number=self.volume_data.volume_number,
            special_version=self.volume_data.special_version,
            issue_number=issue_number
        )
