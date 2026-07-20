from __future__ import annotations

import unittest

from telegram_guardian.moderation import ModerationPolicy, ObservationEngine


def policy() -> ModerationPolicy:
    return ModerationPolicy(
        mode="observe",
        signal_cooldown_seconds=30,
        signal_retention_days=7,
        flood_max_messages=6,
        flood_window_seconds=10,
        duplicate_max_repetitions=3,
        duplicate_window_seconds=60,
        duplicate_minimum_length=8,
        link_max_messages=3,
        link_window_seconds=60,
        mention_max_per_message=5,
    )


def message(
    text: str,
    timestamp: int,
    user_id: int = 7,
    chat_id: int = -1001,
) -> dict[str, object]:
    return {
        "text": text,
        "date": timestamp,
        "chat": {"id": chat_id},
        "from": {"id": user_id},
    }


class ObservationEngineTests(unittest.TestCase):
    def test_detects_flood_at_threshold(self) -> None:
        engine = ObservationEngine(policy())
        result = None
        for index in range(6):
            result = engine.analyze(message(f"mensaje numero {index}", 100 + index))

        self.assertIsNotNone(result)
        self.assertIn("flood", [signal.detector for signal in result.signals])

    def test_detects_repeated_message_using_fingerprint(self) -> None:
        engine = ObservationEngine(policy())
        raw_text = "Promocion privada irrepetible"
        engine.analyze(message(raw_text, 100))
        engine.analyze(message(raw_text.upper(), 101))
        result = engine.analyze(message(f"  {raw_text}  ", 102))

        repeated = next(
            signal for signal in result.signals if signal.detector == "repeticion"
        )
        self.assertNotIn(raw_text, repeated.reason)

    def test_detects_link_burst_across_messages(self) -> None:
        engine = ObservationEngine(policy())
        engine.analyze(message("https://example.com/uno", 100))
        engine.analyze(message("visita www.example.org/dos", 101))
        result = engine.analyze(message("t.me/ejemplo", 102))

        self.assertIn("enlaces", [signal.detector for signal in result.signals])

    def test_many_links_in_one_message_are_one_link_event(self) -> None:
        engine = ObservationEngine(policy())
        result = engine.analyze(
            message(
                "https://example.com/1 https://example.com/2 https://example.com/3",
                100,
            )
        )
        self.assertNotIn("enlaces", [signal.detector for signal in result.signals])

    def test_counts_unique_mentions(self) -> None:
        engine = ObservationEngine(policy())
        result = engine.analyze(
            message("@uno @dos @tres @cuatro @cinco @uno", 100)
        )

        self.assertIn("menciones", [signal.detector for signal in result.signals])

    def test_ignores_commands(self) -> None:
        engine = ObservationEngine(policy())
        for timestamp in range(100, 110):
            result = engine.analyze(message("/estado", timestamp))
        self.assertEqual(result.signals, ())

    def test_thresholds_are_isolated_by_user(self) -> None:
        engine = ObservationEngine(policy())
        for timestamp in range(100, 105):
            engine.analyze(message("mensaje suficientemente largo", timestamp, 7))
        result = engine.analyze(
            message("mensaje suficientemente largo", 105, user_id=8)
        )
        self.assertNotIn("flood", [signal.detector for signal in result.signals])

    def test_random_unique_messages_cannot_grow_fingerprint_state_forever(self) -> None:
        engine = ObservationEngine(
            policy(),
            max_user_windows=100,
            max_duplicate_fingerprints=100,
            max_signal_cooldowns=100,
            cleanup_interval_messages=10_000,
        )
        for index in range(500):
            engine.analyze(message(f"texto aleatorio unico numero {index}", 100))

        self.assertLessEqual(engine.state_sizes()["duplicate_fingerprints"], 100)

    def test_periodic_cleanup_removes_dormant_fingerprints(self) -> None:
        now = [100]
        engine = ObservationEngine(
            policy(),
            max_user_windows=100,
            max_duplicate_fingerprints=100,
            max_signal_cooldowns=100,
            cleanup_interval_messages=1,
            wall_clock=lambda: now[0],
        )
        for index in range(20):
            engine.analyze(message(f"huella antigua distinta {index}", 100))
        now[0] = 1000
        engine.analyze(message("mensaje nuevo despues de expirar", 1000))

        self.assertEqual(engine.state_sizes()["duplicate_fingerprints"], 1)

    def test_out_of_order_duplicate_does_not_create_false_positive(self) -> None:
        now = [100]
        engine = ObservationEngine(
            policy(), cleanup_interval_messages=1, wall_clock=lambda: now[0]
        )
        repeated = "promocion privada suficientemente larga"
        engine.analyze(message(repeated, 100))
        now[0] = 120
        engine.analyze(message("actividad de otro chat", 120, 8, -2002))
        engine.analyze(message(repeated, 90))
        now[0] = 151
        result = engine.analyze(message(repeated, 151))

        self.assertNotIn(
            "repeticion", [signal.detector for signal in result.signals]
        )

    def test_out_of_order_link_does_not_create_false_positive(self) -> None:
        now = [100]
        engine = ObservationEngine(
            policy(), cleanup_interval_messages=1, wall_clock=lambda: now[0]
        )
        engine.analyze(message("https://example.com/primero", 100))
        now[0] = 120
        engine.analyze(message("actividad de otro chat", 120, 8, -2002))
        engine.analyze(message("https://example.com/atrasado", 90))
        now[0] = 161
        result = engine.analyze(message("https://example.com/actual", 161))

        self.assertNotIn("enlaces", [signal.detector for signal in result.signals])

    def test_explicit_compaction_works_without_new_messages(self) -> None:
        now = [100]
        engine = ObservationEngine(policy(), wall_clock=lambda: now[0])
        engine.analyze(message("huella que debe vencer", 100))

        now[0] = 200
        engine.compact()

        self.assertEqual(
            engine.state_sizes(),
            {
                "message_windows": 0,
                "link_windows": 0,
                "duplicate_fingerprints": 0,
                "signal_cooldowns": 0,
            },
        )

    def test_future_timestamp_cannot_empty_another_chat(self) -> None:
        now = [100]
        engine = ObservationEngine(
            policy(), cleanup_interval_messages=1, wall_clock=lambda: now[0]
        )
        repeated = "mensaje repetido con longitud suficiente"
        engine.analyze(message(repeated, 100))
        engine.analyze(message("fecha futura en otro chat", 100_000, 8, -2002))
        now[0] = 101
        engine.analyze(message(repeated, 101))
        now[0] = 102
        result = engine.analyze(message(repeated, 102))

        self.assertIn(
            "repeticion", [signal.detector for signal in result.signals]
        )


if __name__ == "__main__":
    unittest.main()
