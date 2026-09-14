"""
Combinatorial Purged & Embargoed Cross-Validation (CPCV).
Implements Marcos López de Prado's quantitative CV framework to eliminate information
leakage across overlapping labels and serial correlation.
"""

from __future__ import annotations

import itertools

from alphaforge.core.exceptions import DataIntegrityError
from alphaforge.quant_validation.models import CPCVPartition


class CombinatorialPurgedCV:
    """
    Partitions N chronological groups into C(N, k) combinations of train/test sets,
    applying label-overlap purging and post-test embargoes.
    """

    def __init__(
        self,
        n_groups: int = 5,
        k_test_groups: int = 2,
        purge_window_bars: int = 5,
        embargo_window_bars: int = 10,
    ) -> None:
        if n_groups < 3:
            raise DataIntegrityError(f"n_groups must be >= 3, got {n_groups}")
        if not (1 <= k_test_groups < n_groups):
            raise DataIntegrityError(
                f"k_test_groups must be between 1 and {n_groups - 1}, got {k_test_groups}"
            )
        self.n_groups = n_groups
        self.k_test_groups = k_test_groups
        self.purge_window_bars = purge_window_bars
        self.embargo_window_bars = embargo_window_bars

    def split(self, total_bars: int) -> list[CPCVPartition]:
        """
        Partition total_bars into n_groups, then generate all combinations of test groups,
        computing purged and embargoed bar indices.
        """
        min_req = self.n_groups * 10
        if total_bars < min_req:
            raise DataIntegrityError(
                f"Insufficient bars ({total_bars}) for {self.n_groups} groups (minimum {min_req})"
            )

        group_size = total_bars // self.n_groups
        groups: list[tuple[int, int]] = []
        for i in range(self.n_groups):
            start = i * group_size
            end = total_bars if i == self.n_groups - 1 else (i + 1) * group_size
            groups.append((start, end))

        all_group_indices = list(range(self.n_groups))
        test_combinations = list(itertools.combinations(all_group_indices, self.k_test_groups))

        partitions: list[CPCVPartition] = []
        for partition_id, test_comb in enumerate(test_combinations):
            test_indices_set: set[int] = set()
            for g_idx in test_comb:
                g_start, g_end = groups[g_idx]
                test_indices_set.update(range(g_start, g_end))

            purged_indices_set: set[int] = set()
            embargoed_indices_set: set[int] = set()

            for g_idx in test_comb:
                g_start, g_end = groups[g_idx]
                # Purging: bars before test window overlapping forward-looking labels
                purge_start = max(0, g_start - self.purge_window_bars)
                for p_bar in range(purge_start, g_start):
                    if p_bar not in test_indices_set:
                        purged_indices_set.add(p_bar)

                # Embargo: bars after test window eliminating serial correlation leakage
                embargo_end = min(total_bars, g_end + self.embargo_window_bars)
                for e_bar in range(g_end, embargo_end):
                    if e_bar not in test_indices_set and e_bar not in purged_indices_set:
                        embargoed_indices_set.add(e_bar)

            excluded = test_indices_set | purged_indices_set | embargoed_indices_set
            train_indices = [i for i in range(total_bars) if i not in excluded]

            partitions.append(
                CPCVPartition(
                    partition_id=partition_id,
                    total_groups=self.n_groups,
                    test_group_indices=list(test_comb),
                    train_bar_count=len(train_indices),
                    test_bar_count=len(test_indices_set),
                    purged_bar_count=len(purged_indices_set),
                    embargoed_bar_count=len(embargoed_indices_set),
                )
            )

        return partitions
