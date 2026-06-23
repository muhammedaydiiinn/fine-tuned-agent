"""Generate representative seed data directly in PostgreSQL."""
import os
import random
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import Json

DB = dict(
    host=os.getenv("SEED_DB_HOST", "127.0.0.1"),
    port=int(os.getenv("SEED_DB_PORT", "5432")),
    dbname=os.getenv("POSTGRES_DB", "anrufblocker"),
    user=os.getenv("POSTGRES_USER", "anrufblocker"),
    password=os.getenv("POSTGRES_PASSWORD", ""),
)
random.seed(int(os.getenv("SEED", "20260620")))


def json_value(value):
    return Json(value) if value is not None else None

STAGES = ["greeting", "qualification", "objection_handling", "closing", "follow_up"]
INTENTS = ["buy", "cancel", "complain", "info_request", "hard_decline", "soft_decline", "continue"]
EMOTIONS = ["neutral", "happy", "frustrated", "angry", "confused", "interested"]
RISKS = ["low", "medium", "high"]
NEXT_ACTIONS = ["continue", "close", "escalate", "follow_up", "hard_block"]
CORRECTION_TYPES = ["response_edit", "next_action_override", "full_reroute", "tone_fix"]
MODEL_VERSIONS = ["anrufblocker-v12", "anrufblocker-v13", "anrufblocker-v14"]

CUSTOMER_MSGS = [
    "Ich möchte mein Abonnement kündigen.",
    "Was kostet das genau pro Monat?",
    "Nein danke, ich habe kein Interesse.",
    "Können Sie mir mehr Informationen schicken?",
    "Das ist viel zu teuer für mich.",
    "Ich überlege es mir noch mal.",
    "Rufen Sie mich bitte nicht mehr an!",
    "Ich bin zufrieden mit dem aktuellen Anbieter.",
    "Wie lange läuft der Vertrag?",
    "Gibt es eine kostenlose Testphase?",
    "Ich werde das mit meiner Frau besprechen.",
    "Schicken Sie mir bitte ein Angebot zu.",
    "Ich habe bereits ein ähnliches Produkt.",
    "Das klingt interessant, erzählen Sie mehr.",
    "Bitte legen Sie auf, ich bin beschäftigt.",
]

AGENT_RESPONSES = [
    "Natürlich verstehe ich das. Darf ich fragen, was der Hauptgrund für Ihre Entscheidung ist?",
    "Das Angebot kostet 29,99 Euro pro Monat mit allen inkludierten Leistungen.",
    "Ich respektiere das vollständig. Haben Sie vielleicht einen besseren Zeitpunkt?",
    "Sehr gerne sende ich Ihnen detaillierte Informationen zu.",
    "Wir können das Angebot auf 19,99 Euro reduzieren für eine begrenzte Zeit.",
    "Selbstverständlich. Wann wäre ein guter Zeitpunkt für ein Rückruf?",
    "Ich verstehe Ihren Wunsch und werde das notieren.",
    "Was genau gefällt Ihnen an Ihrem aktuellen Anbieter?",
    "Der Vertrag läuft 12 Monate mit monatlicher Kündigung nach 3 Monaten.",
    "Ja, wir bieten eine 30-tägige kostenlose Testphase an.",
    "Natürlich, nehmen Sie sich die Zeit die Sie brauchen.",
    "Ich sende Ihnen sofort ein detailliertes Angebot per E-Mail.",
    "Unser Produkt bietet zusätzlich folgende exklusive Vorteile...",
    "Wunderbar! Ich erkläre Ihnen gerne alle Details.",
    "Alles klar, ich wünsche Ihnen noch einen schönen Tag.",
]

OLD_RESPONSES = [
    "Das tut mir leid zu hören.",
    "Okay, auf Wiedersehen.",
    "Ich kann nichts daran ändern.",
    "Das ist unser bestes Angebot.",
]

CORRECTED_RESPONSES = [
    "Ich verstehe Ihre Bedenken vollständig. Darf ich kurz erklären, warum viele Kunden zunächst ähnlich dachten?",
    "Vielen Dank für Ihre Zeit. Darf ich Sie in zwei Wochen nochmals kontaktieren?",
    "Wir haben tatsächlich flexible Optionen — lassen Sie mich die für Sie passende heraussuchen.",
    "Das respektiere ich. Hätten Sie Einwände, wenn ich Ihnen ein schriftliches Angebot zusende?",
]

def rand_dt(days_back=30):
    base = datetime.now(timezone.utc)
    delta = timedelta(
        days=random.randint(0, days_back),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    return base - delta


def seed(conn):
    cur = conn.cursor()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    # ── Model versions ────────────────────────────────────────────────────────
    print("Creating model versions...")
    mv_ids = {}
    for name in MODEL_VERSIONS:
        cur.execute(
            """
            INSERT INTO model_versions
              (version_name, base_model, lora_path, merged_path, dataset_version,
               eval_status, deployment_status, metadata_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (version_name) DO UPDATE SET version_name=EXCLUDED.version_name
            RETURNING id
            """,
            (
                name,
                "mistralai/Mistral-7B-v0.1",
                f"/models/lora/{name}",
                f"/models/merged/{name}",
                f"dataset-{name.split('v')[1]}",
                random.choice(["pending", "completed", "completed", "failed"]),
                "active" if name == "anrufblocker-v14" else random.choice(["inactive", "inactive", "deprecated"]),
                json_value({"notes": f"Auto-trained {name}"}),
                rand_dt(60),
            ),
        )
        mv_ids[name] = cur.fetchone()[0]

    # ── Eval runs ─────────────────────────────────────────────────────────────
    print("Creating eval runs...")
    for name, mv_id in mv_ids.items():
        for _ in range(random.randint(1, 3)):
            status = random.choice(["completed", "completed", "failed", "running"])
            started = rand_dt(20)
            finished = (started + timedelta(minutes=random.randint(8, 45))) if status in ("completed", "failed") else None
            score = round(random.uniform(0.55, 0.95), 3) if status == "completed" else None
            total = random.randint(80, 200)
            cur.execute(
                """
                INSERT INTO eval_runs
                  (model_version_id, status, metrics_json, progress_current, progress_total,
                   error_message, created_at, started_at, finished_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    mv_id,
                    status,
                    json_value({"quality_score": score}) if score else None,
                    total if status == "completed" else random.randint(0, total),
                    total,
                    "CUDA OOM on batch 42" if status == "failed" else None,
                    started - timedelta(seconds=10),
                    started,
                    finished,
                ),
            )

    # ── Training jobs ─────────────────────────────────────────────────────────
    print("Creating training jobs...")
    for _ in range(8):
        status = random.choice(["completed", "completed", "running", "failed", "pending"])
        started = rand_dt(15) if status != "pending" else None
        finished = (started + timedelta(hours=random.randint(1, 4))) if status in ("completed", "failed") and started else None
        total = random.randint(500, 2000)
        cur.execute(
            """
            INSERT INTO training_jobs
              (job_type, status, input_json, output_json, progress_current, progress_total,
               error_message, created_at, started_at, finished_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                random.choice(["lora_finetune", "full_finetune", "merge"]),
                status,
                json_value({"dataset_version": f"v{random.randint(10,14)}", "epochs": random.randint(1, 5)}),
                json_value({"version_name": random.choice(MODEL_VERSIONS)}) if status == "completed" else None,
                total if status == "completed" else random.randint(0, total),
                total,
                "Loss diverged at step 120" if status == "failed" else None,
                rand_dt(20),
                started,
                finished,
            ),
        )

    # ── Sessions + Turns + Corrections ───────────────────────────────────────
    print("Creating sessions, turns, corrections...")
    session_records = []
    for i in range(25):
        status = "active" if i < 3 else random.choice(["closed"] * 7 + ["active"])
        stage = random.choice(STAGES)
        current_goal = random.choice([
            "confirm_interest",
            "handle_price_objection",
            "collect_callback_consent",
            "move_to_activation",
        ])
        hard_decline = random.randint(0, 3)
        ext_id = f"seed-voice-{run_id}-{i:02d}"
        created = rand_dt(30)
        cur.execute(
            """
            INSERT INTO sessions
              (external_session_id, status, current_stage, current_goal, state_json, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                ext_id,
                status,
                stage,
                current_goal,
                json_value({
                    "stage": stage,
                    "goal": current_goal,
                    "hard_decline_count": hard_decline,
                    "call_attempt": random.randint(1, 4),
                    "identity_confirmed": random.choice([True, False]),
                }),
                created,
                created + timedelta(minutes=random.randint(1, 45)),
            ),
        )
        session_id = cur.fetchone()[0]

        # Turns
        n_turns = random.randint(2, 10)
        turn_ids = []
        for t in range(n_turns):
            turn_created = created + timedelta(seconds=30 * t + random.randint(5, 25))
            total_voice_turn_ms = random.randint(900, 2500)
            cur.execute(
                """
                INSERT INTO turns
                  (session_id, turn_index, customer_text, agent_response,
                   intent, emotion, risk, next_action, allowed_to_continue,
                   model_version, latency_json, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    session_id, t,
                    random.choice(CUSTOMER_MSGS),
                    random.choice(AGENT_RESPONSES),
                    random.choice(INTENTS),
                    random.choice(EMOTIONS),
                    random.choice(RISKS),
                    random.choice(NEXT_ACTIONS),
                    random.choice([True, True, False]),
                    random.choice(MODEL_VERSIONS),
                    json_value({
                        "stt_ms": random.randint(150, 600),
                        "backend_ms": random.randint(280, 1000),
                        "llm_ms": random.randint(300, 1200),
                        "tts_first_audio_ms": random.randint(200, 800),
                        "speech_end_to_first_audio_ms": random.randint(700, 1800),
                        "total_voice_turn_ms": total_voice_turn_ms,
                        "total_ms": total_voice_turn_ms,
                    }),
                    turn_created,
                ),
            )
            turn_ids.append(cur.fetchone()[0])

        # Corrections (some turns)
        for turn_id in random.sample(turn_ids, k=min(2, len(turn_ids))):
            if random.random() < 0.5:
                continue
            cur.execute(
                """
                INSERT INTO corrections
                  (session_id, turn_id, correction_type,
                   old_agent_response, corrected_agent_response,
                   old_next_action, corrected_next_action,
                   apply_immediately, send_to_training, approved,
                   created_by, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    session_id, turn_id,
                    random.choice(CORRECTION_TYPES),
                    random.choice(OLD_RESPONSES),
                    random.choice(CORRECTED_RESPONSES),
                    random.choice(NEXT_ACTIONS),
                    random.choice(NEXT_ACTIONS),
                    random.choice([True, False]),
                    random.choice([True, True, False]),
                    random.choice([True, False]),
                    "admin",
                    rand_dt(10),
                ),
            )
        session_records.append((session_id, ext_id, turn_ids))

    # ── M8 voice event timeline fixtures ────────────────────────────────────
    print("Creating voice events...")
    event_fixtures = [
        ("voice_session_ready", {"state": "listening"}),
        ("transcript_final", {"text": "Was kostet das nach der Testphase?"}),
        ("agent_response", {"state": "speaking"}),
        (
            "interruption_detected",
            {"text": "Moment bitte", "interruption_latency_ms": 438},
        ),
        ("playback_cancelled", {"reason": "customer_speech"}),
        ("duplicate_transcript_ignored", {"text": "Hallo? Hallo?"}),
        ("stale_response_discarded", {"state": "listening"}),
        ("voice_turn_complete", {"state": "listening"}),
    ]
    for session_id, external_id, turn_ids in session_records[:3]:
        turn_id = turn_ids[0] if turn_ids else None
        for sequence, (event_type, payload) in enumerate(event_fixtures, start=1):
            cur.execute(
                """
                INSERT INTO voice_events
                  (session_id, turn_id, event_id, sequence, event_type, payload_json)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    session_id,
                    turn_id,
                    f"{external_id}:{sequence}:seed",
                    sequence,
                    event_type,
                    json_value(payload),
                ),
            )

    # ── Training candidates ───────────────────────────────────────────────────
    print("Creating training candidates...")
    for _ in range(30):
        ctype = random.choice(CORRECTION_TYPES)
        approved = random.choice([True, True, False])
        exported = approved and random.choice([True, False])
        msgs = [
            {"role": "system", "content": "Du bist ein professioneller Verkaufsberater."},
            {"role": "user",   "content": random.choice(CUSTOMER_MSGS)},
            {"role": "assistant", "content": random.choice(CORRECTED_RESPONSES)},
        ]
        cur.execute(
            """
            INSERT INTO training_candidates
              (source_type, source_id, messages_json, metadata_json,
               approved, exported, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                "correction",
                random.randint(1, 10),
                json_value(msgs),
                json_value({"correction_type": ctype}),
                approved,
                exported,
                rand_dt(20),
            ),
        )

    conn.commit()
    cur.close()
    print("Done.")


if __name__ == "__main__":
    conn = psycopg2.connect(**DB)
    seed(conn)
    conn.close()
