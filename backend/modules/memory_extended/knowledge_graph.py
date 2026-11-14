# modules/memory_extended/knowledge_graph.py
"""
Knowledge Graph local para Zephyro.
- Backend: NetworkX (grafo en memoria, serialización JSON)
- Entity extraction con spaCy es_core_news_sm (13MB)
- Fallback regex si spaCy no está disponible
- Tipos: PERSON, PROJECT, TECH, CONCEPT, PLACE, ORG
- Persistencia en storage/knowledge_graph.json
"""
import json
import os
import time
import re
import threading
from typing import Optional, List, Dict, Tuple, Set
from pathlib import Path


class KnowledgeGraph:
    """
    Grafo de conocimiento que extrae y conecta entidades de conversaciones.

    Uso:
        kg = KnowledgeGraph()
        kg.process_message("Estoy trabajando en Zephyro con Python y FastAPI")
        # Crea nodos: Zephyro(PROJECT), Python(TECH), FastAPI(TECH)
        # Crea aristas: Zephyro→usa→Python, Zephyro→usa→FastAPI

        results = kg.query("¿Qué proyectos mencioné?")
        print(kg.find_entity("Zephyro"))
    """

    STORAGE_FILE = "./storage/knowledge_graph.json"
    MAX_NODES = 2000
    MAX_EDGES = 5000

    # Patrones de entidades tech/proyectos (regex para cuando spaCy no está disponible)
    _TECH_PATTERNS = re.compile(
        r'\b(Python|JavaScript|TypeScript|Rust|Go|Java|C\+\+|C#|Ruby|PHP|Swift|Kotlin|'
        r'React|Vue|Angular|FastAPI|Django|Flask|Express|Node\.js|Spring|Laravel|Rails|'
        r'PostgreSQL|MySQL|SQLite|MongoDB|Redis|Elasticsearch|Cassandra|'
        r'Docker|Kubernetes|AWS|GCP|Azure|Linux|Ubuntu|Debian|Arch|macOS|Windows|'
        r'Git|GitHub|GitLab|Bitbucket|CI/CD|Jenkins|GitHub Actions|'
        r'TensorFlow|PyTorch|Keras|scikit-learn|NumPy|Pandas|Jupyter|'
        r'Ollama|LangChain|OpenAI|Anthropic|Gemini|LLaMA|GPT|Claude|'
        r'Playwright|Selenium|Scrapy|BeautifulSoup|Requests|aiohttp|'
        r'Tauri|Electron|Flutter|React Native|'
        r'HTML|CSS|JSON|YAML|TOML|XML|REST|GraphQL|WebSocket|gRPC|'
        r'Vim|VS Code|JetBrains|PyCharm|IntelliJ|Emacs)\b',
        re.IGNORECASE
    )

    _PROJECT_INDICATORS = re.compile(
        r'(?:proyecto|project|app|aplicación|aplicacion|sistema|sistema|herramienta|tool|'
        r'librería|libreria|library|framework|módulo|modulo|repositorio|repo|'
        r'mi proyecto|nuestro proyecto|estoy construyendo|estoy desarrollando|estoy haciendo)\s+'
        r'([A-Z][a-zA-Z0-9_-]+)',
        re.IGNORECASE
    )

    def __init__(self, storage_path: str = None):
        self._storage_path = storage_path or self.STORAGE_FILE
        self._lock = threading.Lock()
        self._spacy_nlp = None
        self._spacy_available: Optional[bool] = None

        # Estructura del grafo en memoria
        self._nodes: Dict[str, dict] = {}
        # key: "Name::TYPE" → {name, type, count, first_seen, last_seen, metadata}

        self._edges: List[dict] = []
        # {from_key, to_key, relation, timestamp, context}

        os.makedirs(os.path.dirname(self._storage_path), exist_ok=True)
        self._load()

        print(f"[KnowledgeGraph] Grafo cargado: {len(self._nodes)} nodos, {len(self._edges)} aristas")

    # ------------------------------------------------------------------
    # spaCy (opcional)
    # ------------------------------------------------------------------

    def _get_spacy(self):
        """Carga spaCy NLP de forma lazy."""
        if self._spacy_available is None:
            try:
                import spacy
                try:
                    self._spacy_nlp = spacy.load("es_core_news_sm")
                    self._spacy_available = True
                    print("[KnowledgeGraph] spaCy es_core_news_sm cargado.")
                except OSError:
                    try:
                        self._spacy_nlp = spacy.load("en_core_web_sm")
                        self._spacy_available = True
                        print("[KnowledgeGraph] spaCy en_core_web_sm cargado (fallback).")
                    except OSError:
                        print("[KnowledgeGraph] spaCy disponible pero sin modelo. "
                              "Instala con: python -m spacy download es_core_news_sm")
                        self._spacy_available = False
            except ImportError:
                print("[KnowledgeGraph] spaCy no instalado. Usando extracción por regex.")
                self._spacy_available = False

        return self._spacy_nlp if self._spacy_available else None

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self):
        """Carga el grafo desde JSON."""
        if not os.path.exists(self._storage_path):
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._nodes = data.get("nodes", {})
                self._edges = data.get("edges", [])
        except Exception as e:
            print(f"[KnowledgeGraph] Error cargando grafo: {e}")

    def _save(self):
        """Guarda el grafo a JSON (operación atómica)."""
        try:
            with self._lock:
                data = {"nodes": self._nodes, "edges": self._edges}
            temp = self._storage_path + ".tmp"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp, self._storage_path)
        except Exception as e:
            print(f"[KnowledgeGraph] Error guardando grafo: {e}")

    def _save_background(self):
        """Guarda en background para no bloquear respuestas."""
        t = threading.Thread(target=self._save, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Creación de nodos/aristas
    # ------------------------------------------------------------------

    def _node_key(self, name: str, entity_type: str) -> str:
        return f"{name.lower()}::{entity_type}"

    def _add_node(self, name: str, entity_type: str, context: str = "") -> str:
        """Agrega o actualiza un nodo. Retorna la key."""
        if len(self._nodes) >= self.MAX_NODES:
            return self._node_key(name, entity_type)

        key = self._node_key(name, entity_type)
        now = time.time()

        with self._lock:
            if key in self._nodes:
                self._nodes[key]["count"] += 1
                self._nodes[key]["last_seen"] = now
            else:
                self._nodes[key] = {
                    "name": name,
                    "type": entity_type,
                    "count": 1,
                    "first_seen": now,
                    "last_seen": now,
                    "contexts": [context[:200]] if context else [],
                }

        return key

    def _add_edge(self, from_key: str, to_key: str, relation: str, context: str = ""):
        """Agrega una arista al grafo."""
        if len(self._edges) >= self.MAX_EDGES:
            return

        # Evitar duplicados recientes
        with self._lock:
            recent_edge = any(
                e["from"] == from_key and e["to"] == to_key and e["relation"] == relation
                for e in self._edges[-100:]
            )
            if not recent_edge:
                self._edges.append({
                    "from": from_key,
                    "to": to_key,
                    "relation": relation,
                    "timestamp": time.time(),
                    "context": context[:200],
                })

    # ------------------------------------------------------------------
    # Extracción de entidades
    # ------------------------------------------------------------------

    def _extract_entities_spacy(self, text: str) -> List[Tuple[str, str]]:
        """Extrae entidades usando spaCy."""
        nlp = self._get_spacy()
        if not nlp:
            return []

        entities = []
        try:
            doc = nlp(text[:1000])  # Limitar a 1000 chars para velocidad
            for ent in doc.ents:
                # Mapear tipos de spaCy a nuestros tipos
                type_map = {
                    "PER": "PERSON", "PERSON": "PERSON",
                    "ORG": "ORG", "GPE": "PLACE", "LOC": "PLACE",
                    "MISC": "CONCEPT", "PRODUCT": "TECH",
                }
                entity_type = type_map.get(ent.label_, "CONCEPT")
                if len(ent.text.strip()) > 1:
                    entities.append((ent.text.strip(), entity_type))
        except Exception as e:
            print(f"[KnowledgeGraph] Error spaCy: {e}")

        return entities

    def _extract_entities_regex(self, text: str) -> List[Tuple[str, str]]:
        """Extrae entidades usando regex (fallback sin spaCy)."""
        entities = []

        # Tecnologías
        for match in self._TECH_PATTERNS.finditer(text):
            entities.append((match.group(0), "TECH"))

        # Proyectos mencionados explícitamente
        for match in self._PROJECT_INDICATORS.finditer(text):
            entities.append((match.group(1), "PROJECT"))

        # Nombres propios simples (palabras que empiezan con mayúscula pero no al inicio de oración)
        # Heurística: después de "con", "usando", "con el proyecto", "sobre", etc.
        project_context = re.findall(
            r'(?:proyecto|proyecto llamado|app llamada?|sistema llamado?)\s+([A-Z][a-zA-Z0-9_-]+)',
            text, re.IGNORECASE
        )
        for name in project_context:
            if name not in [e[0] for e in entities]:
                entities.append((name, "PROJECT"))

        return entities

    def _extract_relations(self, entities: List[Tuple[str, str]], text: str) -> List[Tuple[str, str, str]]:
        """
        Infiere relaciones entre entidades basándose en el contexto.

        Returns:
            Lista de (entity1_key, relation, entity2_key)
        """
        relations = []
        text_lower = text.lower()

        for i, (name1, type1) in enumerate(entities):
            for j, (name2, type2) in enumerate(entities):
                if i == j:
                    continue

                key1 = self._node_key(name1, type1)
                key2 = self._node_key(name2, type2)

                # Detectar relación "usa"
                if type2 == "TECH" and (
                    f"{name1.lower()} usa" in text_lower or
                    f"{name1.lower()} with" in text_lower or
                    f"{name1.lower()} con" in text_lower
                ):
                    relations.append((key1, "usa", key2))

                # Detectar relación "construido con"
                elif type1 == "PROJECT" and type2 == "TECH":
                    relations.append((key1, "usa", key2))

                # Detectar relación "trabaja en"
                elif type1 == "PERSON" and type2 in ("PROJECT", "ORG"):
                    relations.append((key1, "trabaja_en", key2))

        return relations

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def process_message(self, text: str, role: str = "user") -> int:
        """
        Procesa un mensaje y extrae entidades/relaciones.

        Args:
            text: Texto del mensaje
            role: "user" o "assistant"

        Returns:
            Número de nuevas entidades agregadas
        """
        # Solo procesar mensajes del usuario (el assistant puede generar ruido)
        if role != "user":
            return 0

        if not text or len(text.strip()) < 10:
            return 0

        # Extraer entidades
        entities = self._extract_entities_spacy(text)
        if not entities:
            entities = self._extract_entities_regex(text)

        if not entities:
            return 0

        # Deduplicar
        seen = set()
        unique_entities = []
        for name, etype in entities:
            key = (name.lower(), etype)
            if key not in seen:
                seen.add(key)
                unique_entities.append((name, etype))

        # Crear nodos
        new_count = 0
        for name, etype in unique_entities:
            key = self._node_key(name, etype)
            was_new = key not in self._nodes
            self._add_node(name, etype, context=text[:200])
            if was_new:
                new_count += 1

        # Crear aristas (relaciones)
        relations = self._extract_relations(unique_entities, text)
        for from_key, relation, to_key in relations:
            if from_key in self._nodes and to_key in self._nodes:
                self._add_edge(from_key, to_key, relation, context=text[:200])

        # Guardar en background
        if new_count > 0:
            self._save_background()

        return new_count

    def query(self, question: str, limit: int = 10) -> str:
        """
        Consulta el grafo respondiendo a preguntas en lenguaje natural.

        Args:
            question: Pregunta del usuario
            limit: Máximo de resultados

        Returns:
            Respuesta formateada
        """
        q = question.lower()

        # Detectar tipo de consulta
        if any(w in q for w in ["proyecto", "proyectos", "apps", "aplicaciones"]):
            return self._query_by_type("PROJECT", "proyectos", limit)

        elif any(w in q for w in ["tecnología", "tecnologias", "tecnología", "stack", "tech", "herramientas"]):
            return self._query_by_type("TECH", "tecnologías", limit)

        elif any(w in q for w in ["persona", "personas", "gente", "contactos", "equipo"]):
            return self._query_by_type("PERSON", "personas", limit)

        elif any(w in q for w in ["conceptos", "temas", "ideas"]):
            return self._query_by_type("CONCEPT", "conceptos", limit)

        elif any(w in q for w in ["conexiones", "relaciones", "conectado", "relacionado"]):
            # Buscar entidad en la pregunta y mostrar sus conexiones
            entity = self._find_entity_in_query(question)
            if entity:
                return self.get_connections(entity)
            return self._query_all_connections(limit)

        elif any(w in q for w in ["estadísticas", "estadisticas", "resumen del grafo", "cuántos", "cuantos"]):
            return self.get_stats()

        else:
            # Búsqueda general
            entity = self._find_entity_in_query(question)
            if entity:
                return self.find_entity(entity)
            return self.get_stats()

    def _query_by_type(self, entity_type: str, label: str, limit: int) -> str:
        """Retorna entidades de un tipo específico."""
        with self._lock:
            nodes = [
                (key, info) for key, info in self._nodes.items()
                if info["type"] == entity_type
            ]

        if not nodes:
            return f"No encontré {label} en el grafo de conocimiento todavía."

        # Ordenar por frecuencia de mención
        nodes.sort(key=lambda x: x[1]["count"], reverse=True)

        lines = [f"**{label.title()} mencionados** ({len(nodes)} total):\n"]
        for key, info in nodes[:limit]:
            last_seen = time.strftime(
                "%d/%m/%Y", time.localtime(info.get("last_seen", 0))
            )
            lines.append(
                f"• **{info['name']}** — mencionado {info['count']}x "
                f"(último: {last_seen})"
            )

        return "\n".join(lines)

    def get_connections(self, entity_name: str) -> str:
        """Retorna las conexiones de una entidad."""
        # Buscar nodo
        matching_keys = [
            key for key, info in self._nodes.items()
            if info["name"].lower() == entity_name.lower()
        ]

        if not matching_keys:
            # Búsqueda parcial
            matching_keys = [
                key for key, info in self._nodes.items()
                if entity_name.lower() in info["name"].lower()
            ]

        if not matching_keys:
            return f"No encontré '{entity_name}' en el grafo de conocimiento."

        key = matching_keys[0]
        node_info = self._nodes[key]

        with self._lock:
            outgoing = [e for e in self._edges if e["from"] == key]
            incoming = [e for e in self._edges if e["to"] == key]

        lines = [
            f"**{node_info['name']}** ({node_info['type']}) "
            f"— {node_info['count']} menciones\n"
        ]

        if outgoing:
            lines.append("**Relaciones salientes:**")
            for edge in outgoing[:10]:
                to_node = self._nodes.get(edge["to"], {})
                to_name = to_node.get("name", edge["to"])
                lines.append(f"  → {edge['relation']} → **{to_name}**")

        if incoming:
            lines.append("\n**Relaciones entrantes:**")
            for edge in incoming[:10]:
                from_node = self._nodes.get(edge["from"], {})
                from_name = from_node.get("name", edge["from"])
                lines.append(f"  ← {from_name} ← {edge['relation']}")

        if not outgoing and not incoming:
            lines.append("Sin conexiones registradas.")

        return "\n".join(lines)

    def find_entity(self, name: str) -> str:
        """Busca y muestra información de una entidad."""
        q = name.lower()
        matches = [
            (key, info) for key, info in self._nodes.items()
            if q in info["name"].lower()
        ]

        if not matches:
            return f"No encontré '{name}' en el grafo de conocimiento."

        lines = [f"**Resultados para '{name}':**\n"]
        for key, info in matches[:5]:
            first = time.strftime("%d/%m/%Y", time.localtime(info.get("first_seen", 0)))
            last = time.strftime("%d/%m/%Y", time.localtime(info.get("last_seen", 0)))
            lines.append(
                f"• **{info['name']}** ({info['type']})\n"
                f"  Mencionado {info['count']}x | Primera vez: {first} | Último: {last}"
            )

        return "\n".join(lines)

    def _query_all_connections(self, limit: int) -> str:
        """Muestra las entidades más conectadas."""
        with self._lock:
            edges = self._edges.copy()

        # Contar conexiones por nodo
        connection_count: Dict[str, int] = {}
        for edge in edges:
            connection_count[edge["from"]] = connection_count.get(edge["from"], 0) + 1
            connection_count[edge["to"]] = connection_count.get(edge["to"], 0) + 1

        if not connection_count:
            return "El grafo no tiene conexiones registradas todavía."

        # Top nodos por conexiones
        top = sorted(connection_count.items(), key=lambda x: x[1], reverse=True)[:limit]

        lines = ["**Entidades más conectadas:**\n"]
        for key, count in top:
            node = self._nodes.get(key, {})
            lines.append(f"• **{node.get('name', key)}** ({node.get('type', '?')}) — {count} conexiones")

        return "\n".join(lines)

    def _find_entity_in_query(self, text: str) -> Optional[str]:
        """Extrae el nombre de entidad de una query."""
        patterns = [
            r'(?:de|sobre|acerca de|qué es|sobre el|sobre la)\s+["\']?([A-Za-záéíóúñÁÉÍÓÚÑ][A-Za-záéíóúñÁÉÍÓÚÑ0-9_-]+)',
            r'["`\']+([A-Za-záéíóúñÁÉÍÓÚÑ][A-Za-záéíóúñÁÉÍÓÚÑ0-9_-]+)["`\']+',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def get_stats(self) -> str:
        """Retorna estadísticas del grafo."""
        with self._lock:
            total_nodes = len(self._nodes)
            total_edges = len(self._edges)

            # Contar por tipo
            by_type: Dict[str, int] = {}
            for key, info in self._nodes.items():
                t = info["type"]
                by_type[t] = by_type.get(t, 0) + 1

        lines = [
            "**📊 Knowledge Graph - Estadísticas**\n",
            f"• Nodos total: {total_nodes}",
            f"• Aristas (relaciones): {total_edges}",
            "",
            "**Por tipo:**",
        ]
        for t, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  • {t}: {count}")

        return "\n".join(lines)

    def export_json(self) -> dict:
        """Exporta el grafo completo para visualización en frontend (D3.js, etc.)."""
        with self._lock:
            nodes = [
                {
                    "id": key,
                    "label": info["name"],
                    "type": info["type"],
                    "count": info["count"],
                }
                for key, info in self._nodes.items()
            ]
            edges = [
                {
                    "source": e["from"],
                    "target": e["to"],
                    "label": e["relation"],
                }
                for e in self._edges
            ]
        return {"nodes": nodes, "edges": edges}
