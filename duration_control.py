"""Bounded decisions for measured narration duration recovery."""
import math

MIN_SECONDS = 900
MAX_SECONDS = 1200
PREFERRED_MIN = 1020
PREFERRED_MAX = 1140
TARGET_SECONDS = 1080
TEMPO_TARGET = 1198.5
MAX_TEMPO = 1.04
MAX_ATTEMPTS = 3


def valid(seconds):
    return math.isfinite(seconds) and MIN_SECONDS <= seconds <= MAX_SECONDS


def tempo_factor(seconds):
    """Use a tiny speedup only when it can land safely below the hard maximum."""
    if not math.isfinite(seconds) or seconds <= MAX_SECONDS:
        return None
    factor = seconds / TEMPO_TARGET
    return factor if 1 < factor <= MAX_TEMPO else None


def select_revisions(lengths, direction):
    """Choose the fewest long chapters likely to supply the needed duration change."""
    if direction not in ('shorten', 'expand') or not lengths or any(
            not math.isfinite(x) or x <= 0 for x in lengths):
        raise ValueError('Invalid chapter lengths or correction direction')
    total = sum(lengths)
    needed = abs(TARGET_SECONDS - total)
    if needed <= 0:
        return [], 1.0
    # Leave the opening hook and final resolution intact where middle chapters suffice.
    priority = sorted(range(1, len(lengths)-1), key=lambda i: lengths[i], reverse=True)
    priority += [0, len(lengths)-1] if len(lengths) > 1 else [0]
    maximum_change = .38 if direction == 'shorten' else .65
    chosen, capacity = [], 0.0
    for index in priority:
        if index in chosen:
            continue
        chosen.append(index)
        capacity += lengths[index] * maximum_change
        if capacity >= needed:
            break
    selected_seconds = sum(lengths[i] for i in chosen)
    ratio = 1 - needed / selected_seconds if direction == 'shorten' else 1 + needed / selected_seconds
    ratio = max(.62, min(1.65, ratio))
    return sorted(chosen), ratio
