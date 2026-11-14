"""
Clasificador determinístico de comandos shell en 3 tiers.

READ        → libre, log con request_id. Allowlist explícita.
WRITE       → requiere aprobación via WebSocketApprovalGate. **Es el default
              de lo desconocido**: un comando que no cae en ninguna lista
              pide aprobación en vez de ejecutarse solo.
DESTRUCTIVE → bloqueado por defecto; sólo se ejecuta con flag explícito
              en RequestContext y segunda aprobación.

Decisión por regex precompiladas. Función pura, sin side effects.

Los tres gates de `modules/automation/terminal_controller.py` (destructivo,
canal no-Tauri, aprobación) están condicionados al tier, y READ los saltea
los tres. Por eso la cobertura de este archivo ES el modelo de seguridad de
la terminal: cada comando que se le escape queda libre desde cualquier canal,
Telegram incluido.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class CommandTier(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


# ── DESTRUCTIVE — orden importa: chequea primero ───────────────────────
# NOTA: usamos lookahead para flags que empiezan con `-` o `/`, porque \b
# no matchea entre espacio y guión (ambos son non-word chars).
_DESTRUCTIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # Remove-Item con -Recurse y -Force (cualquier orden)
        r"\bremove-item\b(?=[^;\n]*?\s-recurse\b)(?=[^;\n]*?\s-force\b)",
        # rm -rf, rm -fr, rm -Rf, etc.
        r"\brm\s+-(?=[a-z]*r)(?=[a-z]*f)[a-z]+\b",
        r"\brmdir\s+/s\b",
        # del con /s o /q junto con /f (recursivo o quiet + force)
        r"\bdel\b(?=[^;\n]*?\s/[sq]\b)(?=[^;\n]*?\s/f\b)",
        r"\bdel\b\s+/[sq][fq]\b",
        r"\bdel\b\s+/[fq][sq]\b",
        r"\bformat-volume\b",
        r"\bformat\s+[a-z]:",
        r"\bstop-computer\b",
        r"\brestart-computer\b",
        r"\bshutdown\b",
        r"\bclear-disk\b",
        r"\bclear-content\b[^;\n]*\bsystem32\b",
        r"\bset-executionpolicy\b",
        r"\bdisable-windowsoptionalfeature\b",
        r"\bremove-windowsfeature\b",
        r"\bstop-service\b",
        r"\bset-service\b",
        r"\bget-credential\b",
        r"\bconvertto-securestring\b",
        r"\binvoke-expression\b",
        r"\biex\b\s*[\$\(\"'@]",
        # iwr/curl/Invoke-WebRequest piped a iex (sin splitear por |)
        r"\binvoke-webrequest\b[^;\n]*\|\s*iex\b",
        r"\biwr\b[^;\n]*\|\s*iex\b",
        r"\bcurl\b[^;\n]*\|\s*iex\b",
        # git destructivos (lookahead para flags)
        r"\bgit\s+push\b(?=[^;\n]*?\s--force\b)",
        r"\bgit\s+push\b(?=[^;\n]*?\s-f\b)",
        r"\bgit\s+reset\b(?=[^;\n]*?\s--hard\b)",
        r"\bgit\s+clean\b\s+-(?=[a-z]*f)[a-z]+\b",
        r"\bgit\s+branch\b\s+-D\b",
        r"\breg\s+delete\b",
        r"\bregedit\b",
        r"\bnetsh\s+(?!show|dump)",
        r"\bdiskpart\b",
        r"\bbcdedit\b",
        r"\btakeown\b",
        r"\bicacls\b[^;\n]*\s/grant\b",
        # ── Sesión del usuario: cierra TODO sin guardar, en todas las apps.
        # Misma familia que shutdown/restart, que ya estaban acá.
        r"\blogoff\b",
        r"\bshutdown\b\s+/l\b",
        # ── Destrucción irreversible de datos del sistema ──────────────
        r"\bvssadmin\b",              # delete shadows → no hay restauración
        r"\bcipher\b\s+/w",           # wipe de espacio libre
        r"\bclear-recyclebin\b",
        r"\bwevtutil\s+cl\b",         # borra event logs
        r"\binitialize-disk\b",
        # fsutil tiene subcomandos de consulta (`volume diskfree`); solo los
        # que destruyen van acá. El resto cae a WRITE más abajo.
        r"\bfsutil\b[^;\n]*\b(?:setzerodata|dismount|deletejournal"
        r"|createnew|repair\s+set|behavior\s+set)\b",
        r"\bformat\b\s+/",
        r"\bsc\s+delete\b",
        r"\bremove-local(?:user|group)\b",
        r"\bnet\s+(?:user|localgroup)\b(?=[^;\n]*?\s/delete\b)",
        # Eval de un string arbitrario — igual que invoke-expression/iex
        r"\bwmic\b[^;\n]*\bcall\s+create\b",
    )
)

# ── WRITE — modifica estado del sistema ────────────────────────────────
_WRITE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bset-(?!location\b|content\b)\w+",
        r"\bset-content\b",
        r"\bnew-item\b",
        r"\bnew-(?:directory|file|module|object)\b",
        r"\badd-content\b",
        r"\bcopy-item\b",
        r"\bmove-item\b",
        r"\brename-item\b",
        r"\bremove-item\b",
        r"\bout-file\b",
        r"\bnew-itemproperty\b",
        r"\bset-itemproperty\b",
        r"\binstall-package\b",
        r"\binstall-module\b",
        r"\bsave-package\b",
        r"\buninstall-(?:package|module)\b",
        r"\benable-windowsoptionalfeature\b",
        r"\benable-feature\b",
        r"\bstart-service\b",
        r"\brestart-service\b",
        r"\bnew-service\b",
        r"\bnew-localuser\b",
        r"\bnew-localgroup\b",
        r"\b(?:cp|mv|mkdir|touch|rm|rmdir)\b",
        r"\b(?:del|copy|xcopy|robocopy|move|md|ren)\b",
        r"\bgit\s+(?:push|commit|merge|rebase|cherry-pick|stash\s+(?:pop|drop|clear)|checkout\s+--|tag(?:\s+-d)?|am)\b",
        r"\bgit\s+clone\b",
        r"\bgit\s+pull\b",
        r"\bgit\s+fetch\b",
        r"\bgit\s+add\b",
        r"\bgit\s+rm\b",
        r"\bgit\s+mv\b",
        r"\bgit\s+(?:init|remote\s+(?:add|remove|set-url))\b",
        r"\bgh\s+(?:pr\s+(?:create|merge|edit|close|reopen|review)|issue\s+(?:create|edit|close|reopen|comment)|repo\s+(?:create|fork|clone|delete|edit)|release\s+(?:create|delete|edit|upload)|gist\s+(?:create|edit|delete)|secret\s+set|workflow\s+run)\b",
        r"\bnpm\s+(?:install|i|ci|update|uninstall|publish|link|unlink|run|test)\b",
        r"\bpnpm\s+(?:install|i|add|update|remove|publish)\b",
        r"\byarn\s+(?:add|install|remove|publish|upgrade)\b",
        r"\bpip\s+(?:install|uninstall|download)\b",
        r"\bpip3\s+(?:install|uninstall|download)\b",
        r"\bpython\s+-m\s+pip\s+(?:install|uninstall|download)\b",
        r"\buv\s+(?:add|install|sync|pip\s+install)\b",
        r"\bpoetry\s+(?:add|install|update|remove|publish)\b",
        r"\bcargo\s+(?:install|publish|build|run|test|update|add|remove)\b",
        r"\bdotnet\s+(?:add|build|publish|restore|run|test|new)\b",
        r"\bgo\s+(?:install|build|run|test|get|mod\s+(?:tidy|download))\b",
        r"\bdocker\s+(?:run|exec|build|push|pull|rm|rmi|stop|start|kill|compose)\b",
        r"\bchoco\s+(?:install|upgrade|uninstall)\b",
        r"\bscoop\s+(?:install|update|uninstall|bucket\s+(?:add|rm))\b",
        r"\bwinget\s+(?:install|upgrade|uninstall|source\s+(?:add|remove))\b",
        r"\bnpx\s+(?!--?(?:help|version)\b)",
        # ── Matar procesos: pierde el trabajo sin guardar de la app ────
        # No es DESTRUCTIVE (bloqueado) porque matar un proceso es un pedido
        # legítimo de un dev; pero el usuario tiene que VERLO y confirmarlo.
        r"\bstop-process\b",
        r"\btaskkill\b",
        r"\b(?:pkill|killall)\b",
        r"\bkill\s+-\w+",
        # ── Lanzar programas arbitrarios ───────────────────────────────
        r"\bstart-process\b",
        r"\binvoke-item\b",
        r"\bwmic\b",
        # ── Estado del sistema: registro, tareas, servicios, usuarios ──
        r"\breg\s+(?:add|import|copy|restore|load|unload)\b",
        r"\bschtasks\b\s*/(?:create|change|delete|run|end)\b",
        r"\bnet\s+(?:user|localgroup|start|stop|share|use)\b",
        r"\badd-localgroupmember\b",
        r"\bdisable-\w+",
        r"\bsuspend-computer\b",
        r"\bmklink\b",
        r"\battrib\b\s+[+-]",
        r"\bfsutil\b",
        # ── Herramientas que la allowlist de abajo deja pasar como lectura,
        # pero que con ciertos flags escriben. El flag las devuelve a WRITE.
        r"\bsed\s+-[a-z]*i",                       # edición in-place
        r"\bfind\b[^;\n]*\s-(?:delete|exec)\b",
        r"\broute\s+(?:add|delete|change)\b",
        r"\bipconfig\b\s*/(?:release|renew|flushdns|registerdns)",
        r"\barp\b\s+-[ds]\b",
    )
)

# ── READ — allowlist explícita ─────────────────────────────────────────
# Por qué una allowlist y no un default READ: antes, cualquier comando que
# ningún patrón reconociera se ejecutaba libre. Un clasificador cuya omisión
# dice "pasá" no es un guardrail. Medido el 2026-10-01: `logoff`,
# `Stop-Process`, `taskkill`, `vssadmin` y `wmic process call create` caían
# todos en READ, y los tres gates de terminal_controller (destructivo, canal,
# aprobación) están condicionados al tier — READ los saltea los tres.
#
# Ahora: lo que no está acá ni en los patrones de arriba es WRITE, o sea que
# pide aprobación. Agregar un comando de lectura nuevo es barato; dejar un
# comando con efectos fuera de las listas ya no es gratis.
_READ_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # Cmdlets de PowerShell que solo leen o dan forma a la salida.
        #
        # `write-` y `out-` NO van como prefijo: medido el 2026-10-01,
        # `^(?:…|write|out)-\w+` dejaba pasar `Write-EventLog` (escribe en el
        # event log) y `Out-Printer` (imprime). El prefijo del verbo NO
        # expresa el efecto — es el mismo error que permitir todo `Get-*`
        # cuando un `Get-MisCosas` propio puede hacer cualquier cosa.
        r"^(?:get|test|measure|select|where|sort|group|compare|resolve"
        r"|split|join|convertfrom|convertto|format|show|find)-\w+",
        # Las familias write-/out- van por lista explícita.
        r"^write-(?:output|host|verbose|debug|information|progress)\b",
        r"^out-(?:string|host|default|null|gridview)\b",
        # Navegación: cambia el cwd, no el sistema.
        r"^(?:set-location|push-location|pop-location|cd|pushd|popd|chdir)\b",
        # Lectores de archivos y de entorno.
        r"^(?:ls|ll|la|dir|pwd|tree|cat|bat|type|head|tail|more|less|nl|wc"
        r"|file|stat|du|df|diff|cmp|sort|uniq|cut|awk|sed|grep|rg|egrep"
        r"|fgrep|find|fd|which|where|whereis|whoami|hostname|date|time"
        r"|uptime|env|printenv|set|echo|printf|basename|dirname|realpath"
        # certutil NO está: `certutil -urlcache -f <url> <file>` descarga.
        r"|readlink|md5sum|sha256sum|systeminfo|ver|vol|tasklist"
        r"|netstat|ipconfig|ping|tracert|nslookup|arp|route)\b",
        # `git` de lectura. push/commit/etc. ya los agarra _WRITE_PATTERNS.
        r"^git\s+(?:status|log|diff|show|branch|blame|describe|rev-parse"
        r"|rev-list|ls-files|ls-tree|ls-remote|shortlog|reflog|count-objects"
        r"|cat-file|name-rev|whatchanged|grep|bisect\s+log|worktree\s+list"
        r"|stash\s+(?:list|show)|tag\s+-l|remote\s+(?:-v|show)"
        r"|config\s+--get\w*|notes\s+list|for-each-ref|check-ignore)\b",
        r"^git\s+(?:branch|tag|remote|stash|worktree|submodule)\s*$",
        # `gh` de lectura.
        r"^gh\s+(?:\w+\s+)?(?:view|list|status|search|browse|diff|checks)\b",
        r"^gh\s+api\b(?![^;\n]*(?:-X|--method)\s*(?:POST|PUT|PATCH|DELETE))",
        # Consultas de versión / ayuda de cualquier herramienta.
        r"^[\w.-]+\s+(?:--version|-v|-V|--help|-h|version|help)\s*$",
        # Gestores de paquetes: subcomandos que solo consultan.
        r"^(?:npm|pnpm|yarn|pip|pip3|uv|poetry|cargo|go|dotnet|docker"
        r"|choco|scoop|winget|gem|composer)\s+"
        r"(?:list|ls|show|info|search|outdated|view|audit|why|tree|config\s+get"
        r"|licenses|doctor|env|version|--version|ps|images|inspect|logs"
        r"|top|stats|port|diff|history)\b",
        r"^python\d?\s+-m\s+(?:pip\s+(?:list|show|freeze|check)|site)\b",
        r"^pytest\s+--(?:collect-only|version|co)\b",
        # Esperas: bloquean la terminal un rato, no tocan nada.
        r"^(?:start-sleep|sleep|wait-process|wait-job|wait-event)\b",
        # Expresiones puras de PowerShell: aritmética, rangos, lectura de una
        # variable. Se permiten SOLO porque esta allowlist es el último filtro:
        # cualquier cmdlet peligroso dentro del pipeline ya lo agarraron
        # _DESTRUCTIVE_PATTERNS / _WRITE_PATTERNS, que buscan en todo el string.
        # Por eso NO se permiten `(`, `[`, `&` ni `@` al inicio: `& 'calc.exe'`
        # y `[System.IO.File]::Delete(...)` no los cubre ningún patrón.
        r"^\d+(?:\.\.-?\d+)?\b",
        r"^\$(?:env:)?\w+",
    )
)

# ── VETOS — buscados en TODO el string, antes de la allowlist ───────────
# La allowlist de arriba ancla con `^`, o sea que solo describe el PRIMER
# token. Medido el 2026-10-01: 12 de 33 construcciones con efectos pasaban
# como READ por empezar con un token permitido —
#   Get-Process notepad | % { $_.Kill() }
#   1 | % { [IO.File]::Delete("C:/x") }
#   echo hola > C:/importante.txt
#   Get-Process | Tee-Object C:/log.txt
# Un veto que matchea en cualquier posición manda el comando a WRITE, o sea
# que pide aprobación en vez de ejecutarse solo.
#
# ⚠️ ESTO NO ES UNA FRONTERA DE SEGURIDAD, y está medido:
#   Get-Process | % { $_.("Ki" + "ll")() }   ← el nombre del método es una
#   concatenación, así que `\.\s*\w+\s*\(` no matchea. Cada veto que se
#   agrega, el siguiente lo rodea: la lista de formas de escribir lo mismo
#   es ABIERTA. La respuesta real es parsear el AST con
#   `System.Management.Automation.Language.Parser.ParseInput`, donde
#   `$p.Kill()`, `[IO.File]::Delete()` y `$_.("Ki"+"ll")()` son el MISMO
#   nodo (`InvokeMemberExpressionAst`) y se cierra la categoría entera en
#   vez de enumerar ataques. Está planificado como pieza aparte.
_READ_VETOES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # Llamada a método sobre un objeto: $_.Kill(), $f.Delete(), $f.MoveTo()
        r"\.\s*\w+\s*\(",
        # Estático .NET: [IO.File]::Delete(), [Diagnostics.Process]::Start()
        r"::\s*\w+\s*\(",
        # Nombre de método dinámico o construido: .$m() , .("Ki"+"ll")()
        r"\.\s*[\$\(]",
        # Redirección a ARCHIVO, incluida la de un stream numerado.
        # La versión anterior excluía cualquier `>` precedido por dígito para
        # dejar pasar `2>&1`, y con eso dejaba libres `1> file`, `2> file`,
        # `3>> file`… que SÍ escriben (Codex, 2026-10-02). La distinción real
        # es el destino: `N>&M` fusiona streams (inofensivo), `N> algo` abre
        # un archivo. Por eso se exige que lo que sigue NO sea `&`.
        r"(?<![=!<>-])\d?>>?\s*(?!&)\S",
        # Un backtick descalifica la lectura. En un comando de lectura real no
        # aparece casi nunca, y es el vector estándar de ofuscación:
        # `St`op-Process`, `I`EX`. No se intenta interpretarlo.
        r"`",
        # Nombre de miembro entre quotes: $_.'Kill'() , $_."Kill"()
        r"\.\s*['\"]",
        # Escribe un archivo Y pasa el objeto, así que no cae en los write.
        r"\btee-object\b",
        # Operador de invocación y dot-sourcing, EN CUALQUIER POSICIÓN.
        # Antes estaba anclado con `^`, así que el cuerpo de un scriptblock
        # quedaba sin vetar: `Get-Process | % { & ('St'+'op-Process') $_ }`
        # pasaba como lectura porque `% { … }` es una etapa permitida y su
        # contenido no se revisaba (Codex, 2026-10-02).
        # `&` invocando un string, una expresión, una variable o un splat:
        r"&\s*['\"($@]",
        # dot-sourcing al principio de una sentencia o de un bloque
        r"(?:^|[;{|])\s*\.\s+\S",
        # Asignación a una VARIABLE. Tiene que exigir el `$` de la izquierda:
        # un veto sobre `=` suelto rompía 9 de 20 lecturas de uso diario,
        # porque `=` es parte de un montón de flags —
        #   git log --format=%s · rg --type=py · pytest -k 'a and b'
        #   npm ls --depth=0 · pip list --format=json
        #   docker ps --filter status=running
        # Medido el 2026-10-02 (regresión propia, detectada y corregida).
        # Incluye las compuestas (`+=`, `-=`, `*=`, `/=`, `%=`), que evadían
        # el veto simple (Codex, 2026-10-02).
        r"\$(?:\{[^}]+\}|[\w:]+)(?:\.\w+|\[[^\]]*\])*\s*[-+*/%]?=(?!=)",
    )
)

# Etapas válidas DESPUÉS de un pipe. Son control de flujo y formato: no
# tienen efectos propios, y lo que va en sus bloques lo cubren los vetos.
_READ_PIPE_STAGES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^(?:%|\?)\s*[\{\-]",
        r"^(?:foreach-object|where-object|select-object|sort-object"
        r"|group-object|measure-object|compare-object|tee)\b",
        r"^(?:select|sort|group|measure|compare|format|convertto|convertfrom)-\w+",
        r"^out-(?:string|host|default|null|gridview)\b",
        r"^write-(?:output|host|verbose|debug|information)\b",
        r"^(?:select-string|findstr|more|less|head|tail|wc|sort|uniq|grep|rg)\b",
        r"^\{",
    )
)


def _split_top_level_pipes(cmd: str) -> Optional[list[str]]:
    """Separa por `|` solo a nivel tope: respeta {}, (), [], strings y `#`.

    Un split por regex partiría `Where-Object { $_.A -eq 'x|y' }` al medio.

    Devuelve **None** cuando no puede parsear con confianza: quotes sin
    cerrar o delimitadores desbalanceados. Eso es lo que lo hace fallar
    CERRADO — antes, un input malformado simplemente suprimía el split y la
    allowlist anclada al primer token devolvía READ (Codex, 2026-10-02):
        Get-Process "unterminated | Out-Printer   -> read
        Get-Process ) | Out-Printer               -> read
    Un parser que ante la duda dice "pasá" es peor que no tenerlo.

    No intenta interpretar backticks: un backtick en el comando ya lo veta
    `_READ_VETOES`, porque dos backticks seguidos son un backtick literal y
    con eso se puede cerrar una quote que este tokenizador creía abierta.
    """
    stages: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: Optional[str] = None
    for ch in cmd:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == "#":
            # Comentario hasta el fin de línea: lo que sigue no se ejecuta,
            # así que un `|` comentado no puede crear una etapa falsa.
            break
        elif ch in "{([":
            depth += 1
            buf.append(ch)
        elif ch in "})]":
            depth -= 1
            if depth < 0:
                return None  # cierre sin apertura: no se puede confiar
            buf.append(ch)
        elif ch == "|" and depth == 0:
            stages.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if quote is not None or depth != 0:
        return None
    stages.append("".join(buf))
    return [s.strip() for s in stages if s.strip()]


def classify(cmd: str) -> CommandTier:
    """
    Clasifica un comando shell en READ / WRITE / DESTRUCTIVE.

    Si el input contiene operadores de chain (`;`, `&&`, `||`, newline),
    clasifica cada subcomando y devuelve el tier MÁS ALTO. Esto evita que
    `Get-Process; Remove-Item ... -Force` se cuele como READ.

    NO splitea por `|` — el pipe de PowerShell pasa output entre cmdlets,
    no es un separador de comandos. Algunos patrones destructivos (curl|iex)
    necesitan ver el `|` para detectarlos.
    """
    if not cmd or not cmd.strip():
        return CommandTier.READ

    # Primero: chequear patrones destructivos sobre el comando completo,
    # porque algunos (iwr | iex) cruzan el pipe.
    for pat in _DESTRUCTIVE_PATTERNS:
        if pat.search(cmd):
            return CommandTier.DESTRUCTIVE

    parts = re.split(r"\s*(?:;|&&|\|\||\n)\s*", cmd)
    highest = CommandTier.READ

    for part in parts:
        part = part.strip()
        if not part:
            continue
        tier = _classify_single(part)
        if tier == CommandTier.DESTRUCTIVE:
            return CommandTier.DESTRUCTIVE
        if tier == CommandTier.WRITE and highest == CommandTier.READ:
            highest = CommandTier.WRITE

    return highest


def _classify_single(cmd: str) -> CommandTier:
    """Clasificación de un comando atómico (sin chain operators).

    El orden es deliberado: destructivo y write ganan sobre la allowlist de
    lectura, así que un flag peligroso sobre un comando "de lectura"
    (`sed -i`, `find -delete`) no se cuela por estar en la allowlist.

    Lo que no cae en ninguna de las tres listas es WRITE, no READ: pide
    aprobación en vez de ejecutarse solo. Ver el comentario de
    `_READ_PATTERNS` para por qué.
    """
    for pat in _DESTRUCTIVE_PATTERNS:
        if pat.search(cmd):
            return CommandTier.DESTRUCTIVE
    for pat in _WRITE_PATTERNS:
        if pat.search(cmd):
            return CommandTier.WRITE
    # Un veto en CUALQUIER posición descalifica el comando como lectura.
    for pat in _READ_VETOES:
        if pat.search(cmd):
            return CommandTier.WRITE

    # READ exige que TODAS las etapas del pipeline sean de lectura, no solo
    # la primera. Antes se miraba únicamente el primer token, así que
    # `Get-Process | Out-Printer` quedaba libre: la cabeza era `Get-`.
    stages = _split_top_level_pipes(cmd)
    if stages is None:
        # No se pudo parsear: falla CERRADO.
        return CommandTier.WRITE
    if not stages:
        return CommandTier.READ

    for i, stage in enumerate(stages):
        if any(p.search(stage) for p in _READ_PATTERNS):
            continue
        # Las etapas que no son la primera pueden ser control de flujo o
        # formato (`| % { … }`, `| Where-Object …`), que no existen como
        # comando suelto.
        if i > 0 and any(p.search(stage) for p in _READ_PIPE_STAGES):
            continue
        return CommandTier.WRITE

    return CommandTier.READ


def explain(cmd: str) -> dict:
    """
    Devuelve clasificación + el patrón que la disparó. Útil para audit log
    y para que el usuario vea por qué se bloqueó/aprobó un comando.
    """
    tier = classify(cmd)
    matched: list[str] = []

    if tier == CommandTier.DESTRUCTIVE:
        for pat in _DESTRUCTIVE_PATTERNS:
            m = pat.search(cmd)
            if m:
                matched.append(pat.pattern)
                break
    elif tier == CommandTier.WRITE:
        for pat in _WRITE_PATTERNS:
            m = pat.search(cmd)
            if m:
                matched.append(pat.pattern)
                break
    elif tier == CommandTier.READ:
        for pat in _READ_PATTERNS:
            m = pat.search(cmd.lstrip())
            if m:
                matched.append(pat.pattern)
                break

    # WRITE sin patrón = no lo reconoció nadie y cayó al default. Distinguirlo
    # importa en el audit log: es la señal de que falta cobertura.
    unmatched_default = tier == CommandTier.WRITE and not matched

    return {
        "tier": tier.value,
        "matched_patterns": matched,
        "unmatched_default": unmatched_default,
    }
