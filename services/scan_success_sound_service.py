"""Rule-based QR scan success sound selection and playback."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
from services.windows_audio_service import WindowsAudioService


def parse_scan_success_specific_counts(raw: str) -> list[int]:
    values: list[int] = []
    for chunk in (raw or "").replace(";", ",").split(","):
        text = chunk.strip()
        if not text:
            continue
        try:
            number = int(text)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in values:
            values.append(number)
    return values


def format_scan_success_specific_counts(values: Iterable[int]) -> str:
    normalized: list[int] = []
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in normalized:
            normalized.append(number)
    return ", ".join(str(value) for value in normalized)


def _coerce_positive_int(raw: str, default: int = 0) -> int:
    try:
        return max(0, int(str(raw or "").strip()))
    except (TypeError, ValueError):
        return max(0, default)


SCAN_SUCCESS_RULE_TOTAL_PERCENT = 100.0
SCAN_SUCCESS_RULE_DECIMALS = 2


def coerce_scan_success_weight(raw: object, default: float = 1.0, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(str(raw or "").strip()))
    except (TypeError, ValueError):
        return max(minimum, float(default))


def format_scan_success_weight(raw: object) -> str:
    value = round(coerce_scan_success_weight(raw, default=0.0), SCAN_SUCCESS_RULE_DECIMALS)
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.{SCAN_SUCCESS_RULE_DECIMALS}f}".rstrip("0").rstrip(".")


def _rule_weight(rule: ScanSuccessSoundRule) -> float:
    return max(0.0, float(rule.weight or 0.0))


def _is_general_scan_success_rule(rule: ScanSuccessSoundRule) -> bool:
    return (
        bool((rule.sound_path or "").strip())
        and rule.enabled
        and rule.trigger_type == "always"
    )


def _general_scan_success_rule_indices(rules: list[ScanSuccessSoundRule]) -> list[int]:
    return [index for index, rule in enumerate(rules) if _is_general_scan_success_rule(rule)]


def _sorted_weighted_rules(rules: list[ScanSuccessSoundRule]) -> list[ScanSuccessSoundRule]:
    return sorted(
        rules,
        key=lambda rule: (_rule_weight(rule), (rule.name or "").strip(), (rule.sound_path or "").strip()),
        reverse=True,
    )


def _round_distribution(values: list[float], *, total: float) -> list[float]:
    if not values:
        return []
    rounded: list[float] = []
    remaining = round(max(0.0, float(total)), SCAN_SUCCESS_RULE_DECIMALS)
    for index, value in enumerate(values):
        if index == len(values) - 1:
            rounded_value = round(max(0.0, remaining), SCAN_SUCCESS_RULE_DECIMALS)
        else:
            rounded_value = round(max(0.0, float(value)), SCAN_SUCCESS_RULE_DECIMALS)
            rounded_value = min(rounded_value, remaining)
        rounded.append(rounded_value)
        remaining = round(remaining - rounded_value, SCAN_SUCCESS_RULE_DECIMALS)
    return rounded


def normalize_scan_success_general_weights(rules: list[ScanSuccessSoundRule]) -> list[ScanSuccessSoundRule]:
    updated_rules = list(rules)
    general_indices = _general_scan_success_rule_indices(updated_rules)
    if not general_indices:
        return updated_rules
    if len(general_indices) == 1:
        only_index = general_indices[0]
        updated_rules[only_index] = replace(updated_rules[only_index], weight=SCAN_SUCCESS_RULE_TOTAL_PERCENT)
        return updated_rules

    current_weights = [coerce_scan_success_weight(updated_rules[index].weight, default=1.0) for index in general_indices]
    total_weight = sum(current_weights)
    if total_weight <= 0:
        return equalize_scan_success_general_weights(updated_rules)

    scaled_weights = [
        (SCAN_SUCCESS_RULE_TOTAL_PERCENT * weight / total_weight)
        for weight in current_weights
    ]
    for index, weight in zip(general_indices, _round_distribution(scaled_weights, total=SCAN_SUCCESS_RULE_TOTAL_PERCENT)):
        updated_rules[index] = replace(updated_rules[index], weight=weight)
    return updated_rules


def equalize_scan_success_general_weights(rules: list[ScanSuccessSoundRule]) -> list[ScanSuccessSoundRule]:
    updated_rules = list(rules)
    general_indices = _general_scan_success_rule_indices(updated_rules)
    if not general_indices:
        return updated_rules

    equal_share = SCAN_SUCCESS_RULE_TOTAL_PERCENT / float(len(general_indices))
    for index, weight in zip(
        general_indices,
        _round_distribution([equal_share] * len(general_indices), total=SCAN_SUCCESS_RULE_TOTAL_PERCENT),
    ):
        updated_rules[index] = replace(updated_rules[index], weight=weight)
    return updated_rules


def rebalance_scan_success_general_weights_after_edit(
    rules: list[ScanSuccessSoundRule],
    *,
    edited_index: int,
    edited_weight: float,
) -> list[ScanSuccessSoundRule]:
    updated_rules = list(rules)
    general_indices = _general_scan_success_rule_indices(updated_rules)
    if edited_index not in general_indices:
        return normalize_scan_success_general_weights(updated_rules)

    if len(general_indices) == 1:
        updated_rules[edited_index] = replace(updated_rules[edited_index], weight=SCAN_SUCCESS_RULE_TOTAL_PERCENT)
        return updated_rules

    clamped_weight = round(
        min(SCAN_SUCCESS_RULE_TOTAL_PERCENT, max(0.0, float(edited_weight))),
        SCAN_SUCCESS_RULE_DECIMALS,
    )
    other_indices = [index for index in general_indices if index != edited_index]
    remaining_total = round(SCAN_SUCCESS_RULE_TOTAL_PERCENT - clamped_weight, SCAN_SUCCESS_RULE_DECIMALS)
    current_other_weights = [
        coerce_scan_success_weight(updated_rules[index].weight, default=1.0)
        for index in other_indices
    ]
    total_other_weight = sum(current_other_weights)
    if total_other_weight <= 0:
        redistributed = _round_distribution(
            [remaining_total / float(len(other_indices))] * len(other_indices),
            total=remaining_total,
        )
    else:
        redistributed = _round_distribution(
            [
                remaining_total * weight / total_other_weight
                for weight in current_other_weights
            ],
            total=remaining_total,
        )

    for index, weight in zip(other_indices, redistributed):
        updated_rules[index] = replace(updated_rules[index], weight=weight)

    updated_rules[edited_index] = replace(
        updated_rules[edited_index],
        weight=round(
            SCAN_SUCCESS_RULE_TOTAL_PERCENT - sum(redistributed),
            SCAN_SUCCESS_RULE_DECIMALS,
        ),
    )
    return updated_rules


@dataclass(frozen=True)
class ScanSuccessSoundSelection:
    scan_count: int
    rule_name: str
    sound_path: str
    trigger_type: str


@dataclass(frozen=True)
class ScanSuccessSpecialRuleProgress:
    current_count: int
    next_target_count: int
    remaining_count: int
    trigger_type: str
    trigger_label: str
    progress_value: float
    sound_name: str


class ScanSuccessSoundStateStore:
    """Persist the cumulative successful scan count for special triggers."""

    def __init__(self, path: str = ".runtime/scan_success_sound_state.json"):
        self._path = Path(path)

    def load_success_count(self) -> int:
        if not self._path.exists():
            return 0
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        if not isinstance(payload, dict):
            return 0
        return _coerce_positive_int(str(payload.get("success_scan_count", 0)))

    def save_success_count(self, success_scan_count: int) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"success_scan_count": max(0, int(success_scan_count))}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def next_success_count(self) -> int:
        count = self.load_success_count() + 1
        self.save_success_count(count)
        return count


class ScanSuccessSoundService:
    """Select and optionally play a sound when QR receipt processing succeeds."""

    def __init__(
        self,
        *,
        audio_service: WindowsAudioService | None = None,
        state_store: ScanSuccessSoundStateStore | None = None,
        rng: random.Random | None = None,
    ):
        self._audio_service = audio_service
        self._state_store = state_store or ScanSuccessSoundStateStore()
        self._rng = rng or random.Random()

    @staticmethod
    def get_effective_rules(settings: ReceiptSettings | None) -> list[ScanSuccessSoundRule]:
        if settings is None:
            return []

        rules = [
            rule
            for rule in getattr(settings, "qr_scan_success_sound_rules", [])
            if isinstance(rule, ScanSuccessSoundRule) and (rule.sound_path or "").strip()
        ]
        if rules:
            return rules

        legacy_path = (getattr(settings, "qr_scan_success_sound_path", "") or "").strip()
        if not legacy_path:
            return []
        legacy_name = Path(legacy_path).stem or Path(legacy_path).name or "기본 성공음"
        return [ScanSuccessSoundRule(name=legacy_name, sound_path=legacy_path)]

    @staticmethod
    def primary_sound_path(settings: ReceiptSettings | None) -> str:
        rules = ScanSuccessSoundService.get_effective_rules(settings)
        if rules:
            return (rules[0].sound_path or "").strip()
        if settings is None:
            return ""
        return (getattr(settings, "qr_scan_success_sound_path", "") or "").strip()

    @staticmethod
    def _enabled_general_rules(settings: ReceiptSettings | None) -> list[ScanSuccessSoundRule]:
        return [
            rule
            for rule in ScanSuccessSoundService.get_effective_rules(settings)
            if rule.enabled
            and (rule.sound_path or "").strip()
            and rule.trigger_type == "always"
        ]

    def select_for_scan_count(
        self,
        settings: ReceiptSettings | None,
        *,
        scan_count: int,
    ) -> ScanSuccessSoundSelection | None:
        return self.select_for_event(settings, scan_count=scan_count)

    def select_for_event(
        self,
        settings: ReceiptSettings | None,
        *,
        scan_count: int,
        order_number: str = "",
    ) -> ScanSuccessSoundSelection | None:
        _ = order_number
        rules = [
            rule
            for rule in self.get_effective_rules(settings)
            if rule.enabled and (rule.sound_path or "").strip()
        ]
        if not rules:
            return None

        if scan_count > 0:
            specific_rules = [
                rule
                for rule in rules
                if rule.trigger_type == "specific_counts"
                and scan_count in parse_scan_success_specific_counts(rule.trigger_value)
            ]
            if specific_rules:
                return self._to_selection(self._choose_weighted(specific_rules), scan_count)

            recurring_rules = [
                rule
                for rule in rules
                if rule.trigger_type == "every_n"
                and (every_n := _coerce_positive_int(rule.trigger_value)) > 0
                and scan_count % every_n == 0
            ]
            if recurring_rules:
                return self._to_selection(self._choose_weighted(recurring_rules), scan_count)

        general_rules = [rule for rule in rules if rule.trigger_type == "always"]
        if general_rules:
            return self._to_selection(self._choose_weighted(general_rules), scan_count)

        return None

    def record_scan_success_and_select(
        self,
        settings: ReceiptSettings | None,
        *,
        order_number: str = "",
    ) -> ScanSuccessSoundSelection | None:
        return self.select_for_event(
            settings,
            scan_count=self._state_store.next_success_count(),
            order_number=order_number,
        )

    def select_general_for_event(
        self,
        settings: ReceiptSettings | None,
        *,
        scan_count: int | None = None,
    ) -> ScanSuccessSoundSelection | None:
        rules = self._enabled_general_rules(settings)
        if not rules:
            return None
        effective_scan_count = self._state_store.load_success_count() if scan_count is None else max(0, int(scan_count))
        return self._to_selection(self._choose_weighted(rules), effective_scan_count)

    def play_for_scan_success(
        self,
        settings: ReceiptSettings | None,
        *,
        order_number: str = "",
        increment_count: bool = True,
        persist_count: bool = True,
    ) -> ScanSuccessSoundSelection | None:
        current_count = self._state_store.load_success_count()
        if increment_count:
            next_count = current_count + 1
            selection = self.select_for_event(
                settings,
                scan_count=next_count,
                order_number=order_number,
            )
            if selection is None:
                selection = self.select_general_for_event(settings, scan_count=next_count)
            if selection is not None and persist_count:
                self._state_store.save_success_count(next_count)
        else:
            selection = self.select_general_for_event(settings, scan_count=current_count)
        if selection is None:
            return None

        audio_service = self._audio_service
        if audio_service is None:
            return selection

        if audio_service.play_file(selection.sound_path):
            return selection

        fallback_candidates = self._fallback_candidates_after_play_failure(
            settings,
            failed_selection=selection,
        )
        for candidate in fallback_candidates:
            if audio_service.play_file(candidate.sound_path):
                return candidate
        return None

    def load_success_count(self) -> int:
        return self._state_store.load_success_count()

    def save_success_count(self, success_scan_count: int) -> None:
        self._state_store.save_success_count(success_scan_count)

    def describe_next_special_rule_progress(
        self,
        settings: ReceiptSettings | None,
        *,
        current_count: int | None = None,
    ) -> ScanSuccessSpecialRuleProgress | None:
        progress_items = self.describe_special_rule_progresses(
            settings,
            current_count=current_count,
        )
        return progress_items[0] if progress_items else None

    def describe_special_rule_progresses(
        self,
        settings: ReceiptSettings | None,
        *,
        current_count: int | None = None,
    ) -> list[ScanSuccessSpecialRuleProgress]:
        rules = [
            rule
            for rule in self.get_effective_rules(settings)
            if rule.enabled
            and (rule.sound_path or "").strip()
            and rule.trigger_type in {"every_n", "specific_counts"}
        ]
        if not rules:
            return []

        effective_count = self._state_store.load_success_count() if current_count is None else max(0, int(current_count))
        candidates: list[tuple[int, int, ScanSuccessSpecialRuleProgress]] = []

        for rule in rules:
            sound_name = (
                (rule.name or "").strip()
                or Path(rule.sound_path).stem
                or Path(rule.sound_path).name
                or "특수 효과음"
            ).strip()
            if rule.trigger_type == "specific_counts":
                specific_counts = parse_scan_success_specific_counts(rule.trigger_value)
                next_target = next((value for value in specific_counts if value > effective_count), None)
                if next_target is None:
                    continue
                previous_target = 0
                for value in specific_counts:
                    if value >= next_target:
                        break
                    previous_target = value
                span = max(1, next_target - previous_target)
                progress_value = min(1.0, max(0.0, (effective_count - previous_target) / span))
                candidates.append(
                    (
                        next_target,
                        0,
                        ScanSuccessSpecialRuleProgress(
                            current_count=effective_count,
                            next_target_count=next_target,
                            remaining_count=max(0, next_target - effective_count),
                            trigger_type=rule.trigger_type,
                            trigger_label=f"특정 번호 {next_target}",
                            progress_value=progress_value,
                            sound_name=sound_name,
                        ),
                    )
                )
                continue

            every_n = _coerce_positive_int(rule.trigger_value)
            if every_n <= 0:
                continue
            remainder = effective_count % every_n
            remaining_count = every_n if remainder == 0 else every_n - remainder
            next_target = effective_count + remaining_count
            progress_value = 0.0 if remainder == 0 else min(1.0, max(0.0, remainder / every_n))
            candidates.append(
                (
                    next_target,
                    1,
                    ScanSuccessSpecialRuleProgress(
                        current_count=effective_count,
                        next_target_count=next_target,
                        remaining_count=remaining_count,
                        trigger_type=rule.trigger_type,
                        trigger_label=f"N 번마다 {every_n}",
                        progress_value=progress_value,
                        sound_name=sound_name,
                    ),
                )
            )

        if not candidates:
            return []

        candidates.sort(key=lambda item: (item[0], item[1]))
        return [progress for _, _, progress in candidates]

    def _choose_weighted(self, rules: list[ScanSuccessSoundRule]) -> ScanSuccessSoundRule:
        total_weight = sum(_rule_weight(rule) for rule in rules)
        if total_weight <= 0:
            random_index = min(len(rules) - 1, int(self._rng.random() * len(rules)))
            return rules[random_index]
        pick = self._rng.uniform(0, float(total_weight))
        upto = 0.0
        for rule in rules:
            upto += float(_rule_weight(rule))
            if pick <= upto:
                return rule
        return rules[-1]

    def _fallback_candidates_after_play_failure(
        self,
        settings: ReceiptSettings | None,
        *,
        failed_selection: ScanSuccessSoundSelection,
    ) -> list[ScanSuccessSoundSelection]:
        general_rules = [
            rule
            for rule in self._enabled_general_rules(settings)
            if (rule.sound_path or "").strip() != failed_selection.sound_path
        ]
        if not general_rules:
            return []
        return [
            self._to_selection(rule, failed_selection.scan_count)
            for rule in _sorted_weighted_rules(general_rules)
        ]

    def _additional_general_fallbacks(
        self,
        settings: ReceiptSettings | None,
        *,
        excluded_paths: set[str],
        scan_count: int,
    ) -> list[ScanSuccessSoundSelection]:
        rules = [
            rule
            for rule in self.get_effective_rules(settings)
            if rule.enabled
            and (rule.sound_path or "").strip()
            and rule.trigger_type == "always"
            and (rule.sound_path or "").strip() not in excluded_paths
        ]
        return [self._to_selection(rule, scan_count) for rule in _sorted_weighted_rules(rules)]

    @staticmethod
    def _to_selection(rule: ScanSuccessSoundRule, scan_count: int) -> ScanSuccessSoundSelection:
        return ScanSuccessSoundSelection(
            scan_count=scan_count,
            rule_name=(
                (rule.name or "").strip()
                or Path(rule.sound_path).stem
                or Path(rule.sound_path).name
                or "스캔 성공음"
            ).strip(),
            sound_path=(rule.sound_path or "").strip(),
            trigger_type=rule.trigger_type,
        )
