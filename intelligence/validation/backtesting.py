from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestRow:
    cutoff_index: int
    train_matches: int
    test_matches: int


def chronological_splits(total: int, initial_train: int, test_size: int) -> list[BacktestRow]:
    if total <= initial_train:
        return []
    rows = []
    cutoff = initial_train
    while cutoff < total:
        test_end = min(cutoff + test_size, total)
        rows.append(BacktestRow(cutoff_index=cutoff, train_matches=cutoff, test_matches=test_end - cutoff))
        cutoff = test_end
    return rows


def assert_no_future_leakage(train_cutoff_index: int, test_first_index: int) -> None:
    if test_first_index < train_cutoff_index:
        raise AssertionError("Chronological data leakage detected")
