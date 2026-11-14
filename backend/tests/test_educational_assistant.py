#!/usr/bin/env python3
"""
Test del modo Asistente Técnico Educativo de Zephyro
Simula preguntas que podría hacer un padre/tutor sobre el proyecto de hardware IA.
Usa Claude Haiku 4.5 (Anthropic) como backend.
"""
import os
import httpx
import sys
import time

BASE_URL = "http://localhost:8000"
ADMIN_PASSWORD = os.environ.get("ZEPHYRO_ADMIN_PASSWORD", "")

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BLUE   = "\033[94m"
DIM    = "\033[2m"

def c(text, code): return f"{code}{text}{RESET}"

# Preguntas que un padre/tutor escéptico podría hacer
PREGUNTAS_PADRE = [
    (
        "¿Realmente necesitas una GPU para lo que haces? ¿No basta con lo que tienes?",
        ["gpu", "vram", "ia", "local", "segundo", "lento", "proceso"]
    ),
    (
        "¿Por qué no usas simplemente ChatGPT por internet? Es gratis.",
        ["api", "cara", "precio", "depende", "privacidad", "internet", "costo", "gratis"]
    ),
    (
        "¿Cuánto tiempo te va a durar esa GPU? ¿No se va a quedar obsoleta en 2 años?",
        ["año", "util", "dura", "obsoleta", "cuda", "tecnolog"]
    ),
    (
        "¿Por qué cuesta tanto? ¿No hay algo más barato que sirva?",
        ["4060", "minimo", "viab", "alternativ", "barata", "sirva"]
    ),
    (
        "¿Esto es para jugar videojuegos o realmente es para estudiar?",
        ["juego", "ia", "estudio", "aprend", "calcul", "paralelismo", "gpu"]
    ),
]

def login(client: httpx.Client) -> str | None:
    r = client.post(f"{BASE_URL}/api/auth/login", json={"password": ADMIN_PASSWORD})
    d = r.json()
    if d.get("success") and d.get("role") == "admin":
        print(c(f"✓ Login OK — token: {d['token'][:20]}...", GREEN))
        return d["token"]
    print(c(f"✗ Login fallido: {d}", RED))
    return None

def setup_haiku(client: httpx.Client, token: str):
    """Cambiar a Anthropic + claude-haiku-4-5"""
    r1 = client.post(f"{BASE_URL}/api/settings/provider",
                     params={"provider": "anthropic"},
                     headers={"Authorization": f"Bearer {token}"})
    r2 = client.post(f"{BASE_URL}/api/settings/anthropic-model",
                     params={"model": "claude-haiku-4-5"},
                     headers={"Authorization": f"Bearer {token}"})
    p = r1.json().get("provider", "?")
    m = r2.json().get("model", "?")
    print(c(f"✓ Proveedor: {p}  |  Modelo: {m}", GREEN))

def activate_specialty(client: httpx.Client, token: str):
    """Activar modo educational_assistant vía comando /specialty"""
    r = client.post(
        f"{BASE_URL}/api/chat/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "/specialty educational_assistant", "mode": "cloud"},
    )
    resp = r.json().get("response", "")
    ok = "educational_assistant" in resp.lower() or "establecida" in resp.lower()
    icon = c("✓", GREEN) if ok else c("✗", RED)
    print(f"{icon} Specialty: {resp}")
    return ok

def chat(client: httpx.Client, token: str, message: str) -> dict:
    return client.post(
        f"{BASE_URL}/api/chat/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": message, "mode": "cloud"},
        timeout=45.0,
    ).json()

def deactivate_specialty(client: httpx.Client, token: str):
    r = client.post(
        f"{BASE_URL}/api/chat/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "/specialty off", "mode": "cloud"},
    )
    print(c(f"\n✓ Specialty desactivada: {r.json().get('response','')}", DIM))

def check_quality(response: str, keywords: list[str]) -> tuple[bool, list[str]]:
    r_lower = response.lower()
    missing = [kw for kw in keywords if kw not in r_lower]
    return len(missing) == 0, missing


def main():
    print(c("\n" + "="*65, BOLD))
    print(c("  TEST — Modo Asistente Técnico Educativo (Claude Haiku 4.5)", BOLD))
    print(c("  Simula preguntas de padre/tutor sobre hardware IA", BOLD))
    print(c("="*65 + "\n", BOLD))

    client = httpx.Client(timeout=60.0)

    # 1. Auth
    token = login(client)
    if not token:
        sys.exit(1)

    # 2. Configurar Haiku 4.5
    print(c("\n[Setup] Configurando Claude Haiku 4.5...", CYAN))
    setup_haiku(client, token)

    # 3. Activar specialty
    print(c("\n[Setup] Activando modo educational_assistant...", CYAN))
    if not activate_specialty(client, token):
        print(c("✗ No se pudo activar la especialidad. Abortando.", RED))
        sys.exit(1)

    # 4. Ejecutar preguntas
    print(c("\n[TEST] Enviando preguntas del padre/tutor...\n", CYAN))
    results = []

    for i, (pregunta, keywords) in enumerate(PREGUNTAS_PADRE, 1):
        print(c(f"P{i}: {pregunta}", YELLOW))
        t0 = time.perf_counter()
        try:
            d = chat(client, token, pregunta)
            elapsed = time.perf_counter() - t0
            success = d.get("success", False)
            response = d.get("response", d.get("error", ""))
            mode = d.get("mode_used", "?")
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(c(f"  ✗ EXCEPCIÓN: {e}", RED))
            results.append((i, False, str(e), elapsed, []))
            continue

        quality_ok, missing = check_quality(response, keywords)

        # Mostrar respuesta
        print(c(f"  [{elapsed:.1f}s | {mode}]", DIM))
        # Mostrar respuesta completa, con saltos de línea
        lines = response.splitlines()
        for line in lines:
            print(f"  {c(line, BLUE)}")

        if not quality_ok:
            print(c(f"  ⚠ Posibles temas no cubiertos: {missing}", YELLOW))

        # Criterios de PASS: éxito + respuesta >50 chars + tiempo <40s
        passed = success and len(response) > 50 and elapsed < 40.0
        icon = c("✓ PASS", GREEN) if passed else c("✗ FAIL", RED)
        print(f"  {icon}\n")

        results.append((i, passed, response, elapsed, missing))

    # 5. Desactivar specialty
    deactivate_specialty(client, token)

    # 6. Resumen
    print(c("\n" + "="*65, BOLD))
    total = len(results)
    passed_count = sum(1 for _, p, *_ in results if p)
    print(c(f"  RESULTADO: {passed_count}/{total} preguntas respondidas correctamente", BOLD))
    print(c("="*65, BOLD))

    if passed_count < total:
        print(c("\n[FALLOS]", RED))
        for i, passed, response, elapsed, missing in results:
            if not passed:
                print(f"  P{i}: elapsed={elapsed:.1f}s | len={len(response)}")
                if missing:
                    print(f"       Temas no cubiertos: {missing}")
                print(f"       Respuesta: {repr(response[:120])}")

    latencies = [elapsed for _, _, _, elapsed, _ in results]
    if latencies:
        avg = sum(latencies) / len(latencies)
        print(c(f"\n  Latencia promedio Haiku 4.5: {avg:.1f}s", CYAN))

    client.close()
    sys.exit(0 if passed_count == total else 1)


if __name__ == "__main__":
    main()
