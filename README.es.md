# Tacaño 🪙

[English](README.md) · **Español**

**Tu Claude, pero tacaño con los tokens.** ~39% menos gasto en Claude Code, mismo Opus.

```
/plugin marketplace add Nicolas6879/tacano
/plugin install tacano@tacano-marketplace
```

Opus orquesta y [Jev](https://docs.typesafe.ai) (TypeSafe System One), un modelo que solo toma decisiones, decide en ~400 ms y por ~$0.00007 cuándo compactar, qué conservar y qué delegar a trabajadores baratos.


## El problema

En Claude Code casi todo el gasto viene de **releer el contexto** en cada llamada a herramientas: la lectura y la escritura de caché suman ~89% del costo, y la salida del modelo apenas ~11%. Las sesiones largas llegan a 500k–1M tokens y cada respuesta vuelve a leer todo eso. Usar "un modelo más barato" no alcanza: Sonnet 5.5 cobra la lectura de caché igual que Opus 5.5, y **delegar todo a Sonnet costó 22% más** en nuestras mediciones.

## Qué hace

En cada prompt, un hook le hace a Jev 7 preguntas tipadas en un solo request: volumen de trabajo, intención, modelo adecuado, si es sensible, si necesita el historial, si es un límite de tarea, etc. Con esas respuestas, el código decide:

| Situación | Acción |
|---|---|
| Contexto ≥250k **y** Jev detecta que empiezas una tarea nueva (solo si cambiar ahorra ≥$0.50, y máximo una vez por cada 150k de crecimiento), o contexto ≥550k, o caché vencido con ≥300k | Bloquea el prompt (0 tokens) y sugiere `/compact`, o `/clear` si el pedido no necesita el historial. Después reenvías tu mensaje (flecha ↑). Para saltarte el bloqueo, reenvíalo sin compactar. |
| Cualquier compactación (manual o automática) | `PreCompact` guarda ~13k tokens de extractos **literales** (lo más reciente + decisiones, restricciones y valores concretos) y `SessionStart` los reinyecta. |
| Trabajo pesado (≥9 acciones previstas) con ahorro esperado > $0.05 | Pista para Opus: delega la ejecución en `sonnet-worker` o `haiku-worker` (agentes con herramientas restringidas que arrancan livianos) y revisa el resultado. |
| Opiniones, decisiones, secretos, firmas, pagos, publicaciones | Nunca se delegan. |
| Todo lo demás | Silencio. |

Falla en modo seguro: si Jev no responde o hay un error, nunca bloquea por eso. Solo aplica reglas de código, como el tope de 550k.

## Resultados

Replay de 500 turnos reales, llamada por llamada, con el código del plugin y respuestas reales de Jev:

| Métrica | Valor |
|---|---|
| Ahorro de costo | **39.1%** (35.4% con supuestos pesimistas) |
| En sesiones de 500k–1M tokens | ~50% |
| Interrupciones | 1 cada ~38 prompts |
| Lo que sobrevive a una compactación | resumen solo: 50% → resumen + traspaso: ~85–95% |
| Latencia del hook | p50 ~430 ms; `PreCompact` <1 s con un transcript de 26 MB |
| Tests | 39/39 |

Qué se probó y se descartó:
- **RTK**: perdía la información que Claude necesitaba después.
- **context-mode**: en lecturas de código conservó solo el 2% de lo útil.
- **Jev eligiendo qué guardar**: no le ganó a "lo más reciente".

## Instalación

Requisitos:
- Claude Code (CLI o la app de escritorio).
- Python 3 en el PATH como `python`.
- Una API key de TypeSafe en `TYPESAFE_API_KEY`, o una línea `TYPESAFE_API_KEY=...` en `~/.typesafe.env`.

Desde GitHub:

```
/plugin marketplace add Nicolas6879/tacano
/plugin install tacano@tacano-marketplace
```

Desde una copia local:

```
/plugin marketplace add /ruta/a/tacano
/plugin install tacano@tacano-marketplace
```

El plugin empieza a funcionar en la siguiente sesión.

### Recomendado: que Opus orqueste de verdad

Claude Code solo lanza subagentes cuando **tú** lo pides, así que por su cuenta Opus suele ignorar las pistas de delegación de Tacaño (medimos 0 de 2 seguidas). Ejecuta una vez:

```
/tacano:setup
```

Agrega una sección corta y marcada a tu `~/.claude/CLAUDE.md` diciendo que quieres que Opus planee, delegue la ejecución al trabajador `tacano:` sugerido y revise el resultado. Nunca delega opiniones, decisiones, secretos ni publicaciones. Revísalo con `/tacano:setup status` y quítalo con `/tacano:setup off`.

### App de escritorio de Claude (pestaña Code)

Tacaño funciona en la pestaña **Code** de la app de escritorio, en sesiones locales. No funciona en Chat ni en Cowork, ni en sesiones en la nube o en WSL.

1. **Registra el marketplace una vez.** La app no tiene botón para marketplaces propios. Usa una de estas dos formas:
   - en tu terminal: `claude plugin marketplace add Nicolas6879/tacano`
   - o agrega esto dentro de `extraKnownMarketplaces` en `~/.claude/settings.json`:
     ```json
     "tacano-marketplace": { "source": { "source": "github", "repo": "Nicolas6879/tacano" } }
     ```
2. **Instala.** En una sesión de Code, pulsa **+** junto al cuadro del prompt, ve a **Plugins → Add plugin**, elige **Tacaño** y el alcance **user**.
3. **Abre una sesión nueva.** Después lo gestionas en **+ → Plugins → Manage plugins**.

Si la app no muestra el selector de idioma, crea `~/.tacano.json` con `{"lang": "es"}` o `{"lang": "en"}`.

## Configuración

Crea `config.json` en la carpeta de datos del plugin (`~/.claude/plugins/data/tacano-…/`) solo con las claves que quieras cambiar:

```json
{ "compact_at_tokens": 250000, "hard_compact_at_tokens": 550000, "cold_compact_at_tokens": 300000,
  "boundary_threshold": 0.7, "handoff_budget_chars": 48000, "enabled": true }
```

**Idioma:** al activar el plugin, Claude Code te pide elegir English o Español, y puedes cambiarlo después en `/config` (requiere Claude Code v2.1.271 o posterior). Las demás claves y sus valores por defecto están en `plugins/tacano/scripts/jevlib.py` (`DEFAULTS`).

## Medir el ahorro real

```
python plugins/tacano/scripts/report.py
```

Compara el costo por prompt antes y después de instalar (desde tus transcripts locales, subagentes incluidos). También cuenta bloqueos, overrides y traspasos, y muestra si Opus siguió las pistas de delegación.

## Privacidad

- Antes de enviar texto a Jev se redactan claves, tokens, contraseñas y llaves privadas. Solo se envía el prompt, el prompt anterior y el final del último mensaje del asistente.
- El log local (`decisions.jsonl`) guarda hashes y números, nunca el texto de tus prompts.
- Los extractos del traspaso se quedan en tu máquina.

## Estructura

```
.claude-plugin/marketplace.json     marketplace (este repo)
plugins/tacano/
  .claude-plugin/plugin.json
  hooks/hooks.json                  UserPromptSubmit, PreCompact, SessionStart
  scripts/jevlib.py                 config, cliente Jev, política, costos, traspaso
  scripts/gate.py                   decide bloquear, sugerir delegación o nada
  scripts/precompact.py             guarda el traspaso literal
  scripts/session.py                reinyecta el traspaso y el mensaje pendiente
  scripts/report.py                 mide el ahorro real
  agents/                           sonnet-worker, haiku-worker, haiku-browser-worker
  skills/orchestrator/          protocolo para Opus
  skills/setup/ + scripts/setup.py  /tacano:setup, activar a Opus como orquestador
lab/                                simulaciones y evaluación (usan TUS transcripts)
```

## Laboratorio

`lab/` reproduce el análisis con los transcripts locales de quien lo ejecute (`~/.claude/projects`). Los datos generados (`lab/*.json`) contienen texto de prompts y están en `.gitignore`.

```
python lab/mine2.py && python lab/mine3.py      # extrae turnos
python lab/final_sim.py fetch                   # respuestas de Jev a las preguntas del plugin
python lab/final_sim.py grid                    # replay completo + grilla de ajuste
python lab/test_hooks.py                        # 39 tests de casos límite
```

## Limitaciones

- En sesiones cortas (menos de 200k tokens) casi no interviene. Es lo esperado: ahí hay poco que ahorrar.
- La calidad del traspaso se midió con un proxy: rutas, URLs, IDs e identificadores reutilizados después de compactar.
- La pista de delegación es un consejo. `report.py` mide cuánto la sigue Opus.
- Los precios están fijados al 2026-09-25 para Opus/Sonnet 5.5 y al 2026-10-07 para Haiku 5.5, con su tramo de 100k tokens (clave `prices` en la configuración).

## Licencia

MIT
