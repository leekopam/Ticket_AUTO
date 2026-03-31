"""Rule-based scan success sound service tests."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
from services.scan_success_sound_service import (
    ScanSuccessSoundService,
    ScanSuccessSoundStateStore,
    equalize_scan_success_general_weights,
    format_scan_success_specific_counts,
    normalize_scan_success_general_weights,
    parse_scan_success_specific_counts,
    rebalance_scan_success_general_weights_after_edit,
)


class _DeterministicRandom:
    def __init__(self, *, uniform_value: float = 0.0, random_value: float = 0.0):
        self._uniform_value = uniform_value
        self._random_value = random_value

    def uniform(self, _start: float, _end: float) -> float:
        return self._uniform_value

    def random(self) -> float:
        return self._random_value


class _FakeAudioService:
    def __init__(self, *, failing_paths: set[str] | None = None) -> None:
        self.played_paths: list[str] = []
        self._failing_paths = set(failing_paths or set())

    def play_file(self, path: str) -> bool:
        self.played_paths.append(path)
        return path not in self._failing_paths


class ScanSuccessSoundServiceTest(unittest.TestCase):
    def test_specific_count_rules_are_parsed_and_formatted(self) -> None:
        parsed = parse_scan_success_specific_counts("10, 20; 30, 20, x")

        self.assertEqual(parsed, [10, 20, 30])
        self.assertEqual(format_scan_success_specific_counts(parsed), "10, 20, 30")

    def test_legacy_sound_path_becomes_default_rule(self) -> None:
        settings = ReceiptSettings(qr_scan_success_sound_path="C:/sounds/default.mp3")

        rules = ScanSuccessSoundService.get_effective_rules(settings)

        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].sound_path, "C:/sounds/default.mp3")
        self.assertEqual(rules[0].trigger_type, "always")

    def test_rules_take_priority_over_legacy_sound_path_when_both_exist(self) -> None:
        settings = ReceiptSettings(
            qr_scan_success_sound_path="C:/sounds/legacy.mp3",
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(name="rule.mp3", sound_path="C:/sounds/rule.mp3"),
            ],
        )
        service = ScanSuccessSoundService(rng=_DeterministicRandom())

        rules = ScanSuccessSoundService.get_effective_rules(settings)
        selection = service.select_for_scan_count(settings, scan_count=1)

        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].sound_path, "C:/sounds/rule.mp3")
        self.assertEqual(ScanSuccessSoundService.primary_sound_path(settings), "C:/sounds/rule.mp3")
        self.assertIsNotNone(selection)
        self.assertEqual(selection.sound_path, "C:/sounds/rule.mp3")

    def test_specific_count_rule_beats_general_random_pool(self) -> None:
        settings = ReceiptSettings(
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3"),
                ScanSuccessSoundRule(
                    name="tenth.mp3",
                    sound_path="C:/sounds/tenth.mp3",
                    trigger_type="specific_counts",
                    trigger_value="10",
                ),
            ]
        )
        service = ScanSuccessSoundService(rng=_DeterministicRandom())

        selection = service.select_for_scan_count(settings, scan_count=10)

        self.assertIsNotNone(selection)
        self.assertEqual(selection.sound_path, "C:/sounds/tenth.mp3")

    def test_every_n_rule_beats_general_random_pool(self) -> None:
        settings = ReceiptSettings(
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3"),
                ScanSuccessSoundRule(
                    name="five.mp3",
                    sound_path="C:/sounds/five.mp3",
                    trigger_type="every_n",
                    trigger_value="5",
                ),
            ]
        )
        service = ScanSuccessSoundService(rng=_DeterministicRandom())

        selection = service.select_for_scan_count(settings, scan_count=15)

        self.assertIsNotNone(selection)
        self.assertEqual(selection.sound_path, "C:/sounds/five.mp3")

    def test_zero_scan_count_does_not_trigger_special_rules(self) -> None:
        settings = ReceiptSettings(
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3"),
                ScanSuccessSoundRule(
                    name="one.mp3",
                    sound_path="C:/sounds/one.mp3",
                    trigger_type="every_n",
                    trigger_value="1",
                ),
                ScanSuccessSoundRule(
                    name="special.mp3",
                    sound_path="C:/sounds/special.mp3",
                    trigger_type="specific_counts",
                    trigger_value="1",
                ),
            ]
        )
        service = ScanSuccessSoundService(rng=_DeterministicRandom())

        selection = service.select_for_event(settings, scan_count=0)

        self.assertIsNotNone(selection)
        self.assertEqual(selection.sound_path, "C:/sounds/default.mp3")

    def test_general_rules_use_weighted_random_selection(self) -> None:
        settings = ReceiptSettings(
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(name="ko.mp3", sound_path="C:/sounds/ko.mp3", weight=25),
                ScanSuccessSoundRule(name="ja.mp3", sound_path="C:/sounds/ja.mp3", weight=75),
            ]
        )
        service = ScanSuccessSoundService(rng=_DeterministicRandom(uniform_value=30.0))

        selection = service.select_for_scan_count(settings, scan_count=1)

        self.assertIsNotNone(selection)
        self.assertEqual(selection.sound_path, "C:/sounds/ja.mp3")

    def test_play_for_scan_success_uses_state_store_and_audio_service(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_store = ScanSuccessSoundStateStore(str(Path(temp_dir) / "scan_state.json"))
            audio_service = _FakeAudioService()
            service = ScanSuccessSoundService(
                audio_service=audio_service,
                state_store=state_store,
                rng=_DeterministicRandom(),
            )
            settings = ReceiptSettings(
                qr_scan_success_sound_rules=[
                    ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3"),
                ]
            )

            selection = service.play_for_scan_success(settings)

            self.assertIsNotNone(selection)
            self.assertEqual(selection.scan_count, 1)
            self.assertEqual(audio_service.played_paths, ["C:/sounds/default.mp3"])
            self.assertEqual(state_store.load_success_count(), 1)

    def test_play_for_scan_success_can_skip_count_persist_until_later_commit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_store = ScanSuccessSoundStateStore(str(Path(temp_dir) / "scan_state.json"))
            audio_service = _FakeAudioService()
            service = ScanSuccessSoundService(
                audio_service=audio_service,
                state_store=state_store,
                rng=_DeterministicRandom(),
            )
            settings = ReceiptSettings(
                qr_scan_success_sound_rules=[
                    ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3"),
                ]
            )

            selection = service.play_for_scan_success(settings, persist_count=False)

            self.assertIsNotNone(selection)
            self.assertEqual(selection.scan_count, 1)
            self.assertEqual(audio_service.played_paths, ["C:/sounds/default.mp3"])
            self.assertEqual(state_store.load_success_count(), 0)

    def test_play_for_scan_success_accepts_order_number_keyword(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_store = ScanSuccessSoundStateStore(str(Path(temp_dir) / "scan_state.json"))
            audio_service = _FakeAudioService()
            service = ScanSuccessSoundService(
                audio_service=audio_service,
                state_store=state_store,
                rng=_DeterministicRandom(),
            )
            settings = ReceiptSettings(
                qr_scan_success_sound_rules=[
                    ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3"),
                ]
            )

            selection = service.play_for_scan_success(
                settings,
                order_number="980235_15033183",
            )

            self.assertIsNotNone(selection)
            self.assertEqual(selection.scan_count, 1)
            self.assertEqual(audio_service.played_paths, ["C:/sounds/default.mp3"])
            self.assertEqual(state_store.load_success_count(), 1)

    def test_play_for_scan_success_without_increment_uses_general_rule_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_store = ScanSuccessSoundStateStore(str(Path(temp_dir) / "scan_state.json"))
            state_store.save_success_count(38)
            audio_service = _FakeAudioService()
            service = ScanSuccessSoundService(
                audio_service=audio_service,
                state_store=state_store,
                rng=_DeterministicRandom(),
            )
            settings = ReceiptSettings(
                qr_scan_success_sound_rules=[
                    ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3"),
                    ScanSuccessSoundRule(
                        name="nth.mp3",
                        sound_path="C:/sounds/nth.mp3",
                        trigger_type="specific_counts",
                        trigger_value="39",
                    ),
                ]
            )

            selection = service.play_for_scan_success(
                settings,
                increment_count=False,
            )

            self.assertIsNotNone(selection)
            self.assertEqual(selection.scan_count, 38)
            self.assertEqual(selection.sound_path, "C:/sounds/default.mp3")
            self.assertEqual(audio_service.played_paths, ["C:/sounds/default.mp3"])
            self.assertEqual(state_store.load_success_count(), 38)

    def test_play_for_scan_success_without_increment_uses_general_rule_when_count_is_zero(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_store = ScanSuccessSoundStateStore(str(Path(temp_dir) / "scan_state.json"))
            audio_service = _FakeAudioService()
            service = ScanSuccessSoundService(
                audio_service=audio_service,
                state_store=state_store,
                rng=_DeterministicRandom(),
            )
            settings = ReceiptSettings(
                qr_scan_success_sound_rules=[
                    ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3"),
                    ScanSuccessSoundRule(
                        name="first.mp3",
                        sound_path="C:/sounds/first.mp3",
                        trigger_type="every_n",
                        trigger_value="1",
                    ),
                ]
            )

            selection = service.play_for_scan_success(
                settings,
                increment_count=False,
            )

            self.assertIsNotNone(selection)
            self.assertEqual(selection.sound_path, "C:/sounds/default.mp3")
            self.assertEqual(audio_service.played_paths, ["C:/sounds/default.mp3"])
            self.assertEqual(state_store.load_success_count(), 0)

    def test_describe_next_special_rule_progress_chooses_nearest_special_rule(self) -> None:
        service = ScanSuccessSoundService(rng=_DeterministicRandom())
        settings = ReceiptSettings(
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3"),
                ScanSuccessSoundRule(
                    name="nth.mp3",
                    sound_path="C:/sounds/nth.mp3",
                    trigger_type="every_n",
                    trigger_value="10",
                ),
                ScanSuccessSoundRule(
                    name="special.mp3",
                    sound_path="C:/sounds/special.mp3",
                    trigger_type="specific_counts",
                    trigger_value="39",
                ),
            ]
        )

        progress = service.describe_next_special_rule_progress(settings, current_count=37)

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual(progress.next_target_count, 39)
        self.assertEqual(progress.remaining_count, 2)
        self.assertEqual(progress.trigger_type, "specific_counts")
        self.assertEqual(progress.trigger_label, "특정 번호 39")

    def test_describe_next_special_rule_progress_resets_after_every_n_hit(self) -> None:
        service = ScanSuccessSoundService(rng=_DeterministicRandom())
        settings = ReceiptSettings(
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(
                    name="nth.mp3",
                    sound_path="C:/sounds/nth.mp3",
                    trigger_type="every_n",
                    trigger_value="10",
                ),
            ]
        )

        progress = service.describe_next_special_rule_progress(settings, current_count=20)

        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual(progress.next_target_count, 30)
        self.assertEqual(progress.remaining_count, 10)
        self.assertEqual(progress.trigger_label, "N 번마다 10")
        self.assertEqual(progress.progress_value, 0.0)

    def test_describe_special_rule_progresses_returns_all_visible_special_rules(self) -> None:
        service = ScanSuccessSoundService(rng=_DeterministicRandom())
        settings = ReceiptSettings(
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(
                    name="[테토] N번째",
                    sound_path="C:/sounds/nth.mp3",
                    trigger_type="every_n",
                    trigger_value="10",
                ),
                ScanSuccessSoundRule(
                    name="[테토] 특정 번호",
                    sound_path="C:/sounds/special.mp3",
                    trigger_type="specific_counts",
                    trigger_value="39,77",
                ),
            ]
        )

        progress_items = service.describe_special_rule_progresses(settings, current_count=37)

        self.assertEqual(len(progress_items), 2)
        self.assertEqual(progress_items[0].trigger_type, "specific_counts")
        self.assertEqual(progress_items[0].next_target_count, 39)
        self.assertEqual(progress_items[1].trigger_type, "every_n")
        self.assertEqual(progress_items[1].next_target_count, 40)
        self.assertEqual(progress_items[0].sound_name, "[테토] 특정 번호")
        self.assertEqual(progress_items[1].sound_name, "[테토] N번째")

    def test_describe_special_rule_progresses_keeps_multiple_rules_with_same_type(self) -> None:
        service = ScanSuccessSoundService(rng=_DeterministicRandom())
        settings = ReceiptSettings(
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(
                    name="special-1.mp3",
                    sound_path="C:/sounds/special-1.mp3",
                    trigger_type="specific_counts",
                    trigger_value="39",
                ),
                ScanSuccessSoundRule(
                    name="special-2.mp3",
                    sound_path="C:/sounds/special-2.mp3",
                    trigger_type="specific_counts",
                    trigger_value="39",
                ),
            ]
        )

        progress_items = service.describe_special_rule_progresses(settings, current_count=37)

        self.assertEqual(len(progress_items), 2)
        self.assertEqual(progress_items[0].sound_name, "special-1.mp3")
        self.assertEqual(progress_items[1].sound_name, "special-2.mp3")

    def test_describe_special_rule_progresses_ignores_disabled_rules(self) -> None:
        service = ScanSuccessSoundService(rng=_DeterministicRandom())
        settings = ReceiptSettings(
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(
                    name="enabled.mp3",
                    sound_path="C:/sounds/enabled.mp3",
                    trigger_type="specific_counts",
                    trigger_value="39",
                    enabled=True,
                ),
                ScanSuccessSoundRule(
                    name="disabled.mp3",
                    sound_path="C:/sounds/disabled.mp3",
                    trigger_type="specific_counts",
                    trigger_value="90",
                    enabled=False,
                ),
            ]
        )

        progress_items = service.describe_special_rule_progresses(settings, current_count=37)

        self.assertEqual(len(progress_items), 1)
        self.assertEqual(progress_items[0].sound_name, "enabled.mp3")

    def test_play_for_scan_success_falls_back_to_general_rule_when_special_playback_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_store = ScanSuccessSoundStateStore(str(Path(temp_dir) / "scan_state.json"))
            state_store.save_success_count(1)
            audio_service = _FakeAudioService(failing_paths={"C:/sounds/special.mp3"})
            service = ScanSuccessSoundService(
                audio_service=audio_service,
                state_store=state_store,
                rng=_DeterministicRandom(),
            )
            settings = ReceiptSettings(
                qr_scan_success_sound_rules=[
                    ScanSuccessSoundRule(name="default.mp3", sound_path="C:/sounds/default.mp3", weight=100),
                    ScanSuccessSoundRule(
                        name="special.mp3",
                        sound_path="C:/sounds/special.mp3",
                        trigger_type="specific_counts",
                        trigger_value="2",
                    ),
                ]
            )

            selection = service.play_for_scan_success(settings)

            self.assertIsNotNone(selection)
            assert selection is not None
            self.assertEqual(selection.sound_path, "C:/sounds/default.mp3")
            self.assertEqual(
                audio_service.played_paths,
                ["C:/sounds/special.mp3", "C:/sounds/default.mp3"],
            )

    def test_play_for_scan_success_tries_other_general_rule_when_first_general_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_store = ScanSuccessSoundStateStore(str(Path(temp_dir) / "scan_state.json"))
            audio_service = _FakeAudioService(failing_paths={"C:/sounds/ko.mp3"})
            service = ScanSuccessSoundService(
                audio_service=audio_service,
                state_store=state_store,
                rng=_DeterministicRandom(uniform_value=10.0),
            )
            settings = ReceiptSettings(
                qr_scan_success_sound_rules=[
                    ScanSuccessSoundRule(name="ko.mp3", sound_path="C:/sounds/ko.mp3", weight=80),
                    ScanSuccessSoundRule(name="ja.mp3", sound_path="C:/sounds/ja.mp3", weight=20),
                ]
            )

            selection = service.play_for_scan_success(settings, increment_count=False)

            self.assertIsNotNone(selection)
            assert selection is not None
            self.assertEqual(selection.sound_path, "C:/sounds/ja.mp3")
            self.assertEqual(audio_service.played_paths, ["C:/sounds/ko.mp3", "C:/sounds/ja.mp3"])

    def test_normalize_general_weights_preserves_relative_ratio_within_100_percent(self) -> None:
        rules = [
            ScanSuccessSoundRule(name="a.mp3", sound_path="C:/sounds/a.mp3", weight=1),
            ScanSuccessSoundRule(name="b.mp3", sound_path="C:/sounds/b.mp3", weight=3),
            ScanSuccessSoundRule(
                name="nth.mp3",
                sound_path="C:/sounds/nth.mp3",
                trigger_type="every_n",
                trigger_value="10",
                weight=9,
            ),
        ]

        normalized = normalize_scan_success_general_weights(rules)

        self.assertEqual(normalized[0].weight, 25.0)
        self.assertEqual(normalized[1].weight, 75.0)
        self.assertEqual(normalized[2].weight, 9)

    def test_equalize_general_weights_ignores_special_rules(self) -> None:
        rules = [
            ScanSuccessSoundRule(name="a.mp3", sound_path="C:/sounds/a.mp3"),
            ScanSuccessSoundRule(name="b.mp3", sound_path="C:/sounds/b.mp3"),
            ScanSuccessSoundRule(
                name="nth.mp3",
                sound_path="C:/sounds/nth.mp3",
                trigger_type="every_n",
                trigger_value="10",
                weight=7,
            ),
        ]

        equalized = equalize_scan_success_general_weights(rules)

        self.assertEqual(equalized[0].weight, 50.0)
        self.assertEqual(equalized[1].weight, 50.0)
        self.assertEqual(equalized[2].weight, 7)

    def test_manual_general_probability_edit_rebalances_other_general_rules(self) -> None:
        rules = [
            ScanSuccessSoundRule(name="a.mp3", sound_path="C:/sounds/a.mp3", weight=50),
            ScanSuccessSoundRule(name="b.mp3", sound_path="C:/sounds/b.mp3", weight=30),
            ScanSuccessSoundRule(name="c.mp3", sound_path="C:/sounds/c.mp3", weight=20),
            ScanSuccessSoundRule(
                name="nth.mp3",
                sound_path="C:/sounds/nth.mp3",
                trigger_type="every_n",
                trigger_value="10",
                weight=4,
            ),
        ]

        rebalanced = rebalance_scan_success_general_weights_after_edit(
            rules,
            edited_index=0,
            edited_weight=40,
        )

        self.assertEqual(rebalanced[0].weight, 40.0)
        self.assertEqual(rebalanced[1].weight, 36.0)
        self.assertEqual(rebalanced[2].weight, 24.0)
        self.assertEqual(rebalanced[3].weight, 4)


if __name__ == "__main__":
    unittest.main()
