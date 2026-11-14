"""
Sistema de Historial de Conversaciones con Clasificación de Importancia
Almacena TODAS las conversaciones, pero solo las importantes van a memoria vectorial
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path

class ConversationHistory:
    def __init__(self, db_path: str = "storage/conversation_history.db"):
        """Inicializar base de datos de historial"""
        self.db_path = db_path
        
        # Crear directorio si no existe
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Inicializar base de datos
        self._init_db()
        
        print(f"[History] Sistema de Historial de Conversaciones activo")
        print(f"[History] Base de datos: {db_path}")
        
        # Mostrar estadísticas
        stats = self.get_statistics()
        print(f"[History] Total conversaciones: {stats['total_conversations']}")
        print(f"[History] Conversaciones importantes: {stats['important_conversations']}")
    
    def _init_db(self):
        """Crear tablas si no existen"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de conversaciones
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                conversation_title TEXT,
                user_role TEXT,
                user_name TEXT,
                timestamp TEXT,
                user_input TEXT,
                assistant_response TEXT,
                importance_level TEXT DEFAULT 'trivial',
                importance_score REAL DEFAULT 0.0,
                importance_reasoning TEXT,
                saved_to_memory BOOLEAN DEFAULT 0,
                manually_marked BOOLEAN DEFAULT 0,
                intent TEXT,
                complexity TEXT,
                elapsed_time REAL,
                mode_used TEXT,
                metadata TEXT,
                message_count INTEGER DEFAULT 1
            )
        ''')
        
        # Migrar columna conversation_title si no existe
        cursor.execute("PRAGMA table_info(conversations)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'conversation_title' not in columns:
            cursor.execute('ALTER TABLE conversations ADD COLUMN conversation_title TEXT')
            print('[History] Columna conversation_title añadida')
        if 'message_count' not in columns:
            cursor.execute('ALTER TABLE conversations ADD COLUMN message_count INTEGER DEFAULT 1')
            print('[History] Columna message_count añadida')
        
        # Índices para búsquedas eficientes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON conversations(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_importance ON conversations(importance_level)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_role ON conversations(user_role)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_session ON conversations(session_id)')
        
        conn.commit()
        conn.close()
    
    def _generate_title(self, user_input: str, max_length: int = 50) -> str:
        """Generar título basado en la pregunta del usuario"""
        # Limpiar y truncar
        title = user_input.strip()
        
        # Si es muy corto, devolver como está
        if len(title) <= max_length:
            return title
        
        # Truncar en palabra completa
        title = title[:max_length]
        last_space = title.rfind(' ')
        if last_space > max_length // 2:
            title = title[:last_space]
        
        return title + '...'
    
    def add_conversation(
        self,
        user_input: str,
        assistant_response: str,
        importance_level: str = 'trivial',
        importance_score: float = 0.0,
        importance_reasoning: str = '',
        saved_to_memory: bool = False,
        user_role: str = 'guest',
        user_name: str = None,
        session_id: str = None,
        intent: str = None,
        complexity: str = None,
        elapsed_time: float = None,
        mode_used: str = None,
        metadata: Dict = None
    ) -> int:
        """
        Agregar conversación al historial
        
        importance_level: 'trivial', 'moderate', 'important', 'critical'
        importance_score: 0.0 a 1.0
        """
        # Generar título automáticamente
        conversation_title = self._generate_title(user_input)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        timestamp = datetime.now().isoformat()
        metadata_json = json.dumps(metadata) if metadata else None
        
        cursor.execute('''
            INSERT INTO conversations (
                session_id, conversation_title, user_role, user_name, timestamp,
                user_input, assistant_response,
                importance_level, importance_score, importance_reasoning,
                saved_to_memory, manually_marked,
                intent, complexity, elapsed_time, mode_used, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id, conversation_title, user_role, user_name, timestamp,
            user_input, assistant_response,
            importance_level, importance_score, importance_reasoning,
            saved_to_memory, False,
            intent, complexity, elapsed_time, mode_used, metadata_json
        ))
        
        conversation_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return conversation_id
    
    def mark_as_important(self, conversation_id: int, reasoning: str = "Marcado manualmente por el usuario"):
        """Marcar conversación como importante manualmente"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE conversations
            SET importance_level = 'important',
                importance_score = 1.0,
                importance_reasoning = ?,
                manually_marked = 1
            WHERE id = ?
        ''', (reasoning, conversation_id))
        
        conn.commit()
        conn.close()
    
    def mark_as_memorized(self, conversation_id: int):
        """Marcar que la conversación fue guardada en memoria vectorial"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE conversations
            SET saved_to_memory = 1
            WHERE id = ?
        ''', (conversation_id,))
        
        conn.commit()
        conn.close()
    
    def get_recent_conversations(
        self,
        limit: int = 50,
        user_role: str = None,
        importance_level: str = None,
        session_id: str = None
    ) -> List[Dict]:
        """Obtener conversaciones recientes"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = 'SELECT * FROM conversations WHERE 1=1'
        params = []
        
        if user_role:
            query += ' AND user_role = ?'
            params.append(user_role)
        
        if importance_level:
            query += ' AND importance_level = ?'
            params.append(importance_level)
        
        if session_id:
            query += ' AND session_id = ?'
            params.append(session_id)
        
        query += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        conversations = []
        for row in rows:
            conv = dict(row)
            if conv['metadata']:
                conv['metadata'] = json.loads(conv['metadata'])
            conversations.append(conv)
        
        conn.close()
        return conversations
    
    def get_grouped_conversations(
        self,
        limit: int = 50,
        user_role: str = None
    ) -> List[Dict]:
        """Obtener conversaciones agrupadas por session_id con toda la información"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Obtener todas las conversaciones agrupadas por session_id
        # Cada session_id es una conversación completa
        query = '''
            SELECT 
                MIN(id) as id,
                session_id,
                COALESCE(MIN(CASE WHEN conversation_title IS NOT NULL AND conversation_title != '' THEN conversation_title END), MIN(user_input)) as conversation_title,
                MIN(timestamp) as timestamp,
                COUNT(*) as message_count,
                MAX(importance_level) as importance_level,
                MAX(saved_to_memory) as saved_to_memory,
                GROUP_CONCAT(
                    json_object(
                        'user_message', user_input,
                        'assistant_response', assistant_response,
                        'timestamp', timestamp
                    ), '|||'
                ) as messages_json
            FROM conversations
            WHERE 1=1
        '''
        params = []
        
        if user_role:
            query += ' AND user_role = ?'
            params.append(user_role)
        
        query += '''
            GROUP BY session_id
            ORDER BY timestamp DESC
            LIMIT ?
        '''
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        conversations = []
        for row in rows:
            conv = dict(row)
            
            # Parsear mensajes
            if conv.get('messages_json'):
                try:
                    messages_raw = conv['messages_json'].split('|||')
                    conv['messages'] = [json.loads(msg) for msg in messages_raw]
                except:
                    conv['messages'] = []
            else:
                conv['messages'] = []
            
            del conv['messages_json']
            conversations.append(conv)
        
        conn.close()
        return conversations
    
    def get_conversation(self, conversation_id: int) -> Optional[Dict]:
        """Obtener conversación específica"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM conversations WHERE id = ?', (conversation_id,))
        row = cursor.fetchone()
        
        if row:
            conv = dict(row)
            if conv['metadata']:
                conv['metadata'] = json.loads(conv['metadata'])
            conn.close()
            return conv
        
        conn.close()
        return None
    
    def search_conversations(
        self,
        query: str,
        limit: int = 20,
        importance_level: str = None
    ) -> List[Dict]:
        """Buscar conversaciones por texto"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        search_query = '''
            SELECT * FROM conversations
            WHERE (user_input LIKE ? OR assistant_response LIKE ?)
        '''
        params = [f'%{query}%', f'%{query}%']
        
        if importance_level:
            search_query += ' AND importance_level = ?'
            params.append(importance_level)
        
        search_query += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(search_query, params)
        rows = cursor.fetchall()
        
        conversations = []
        for row in rows:
            conv = dict(row)
            if conv['metadata']:
                conv['metadata'] = json.loads(conv['metadata'])
            conversations.append(conv)
        
        conn.close()
        return conversations
    
    def get_statistics(self) -> Dict:
        """Obtener estadísticas del historial"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Total de conversaciones
        cursor.execute('SELECT COUNT(*) FROM conversations')
        total = cursor.fetchone()[0]
        
        # Por nivel de importancia
        cursor.execute('''
            SELECT importance_level, COUNT(*) 
            FROM conversations 
            GROUP BY importance_level
        ''')
        by_importance = dict(cursor.fetchall())
        
        # Guardadas en memoria
        cursor.execute('SELECT COUNT(*) FROM conversations WHERE saved_to_memory = 1')
        in_memory = cursor.fetchone()[0]
        
        # Marcadas manualmente
        cursor.execute('SELECT COUNT(*) FROM conversations WHERE manually_marked = 1')
        manually_marked = cursor.fetchone()[0]
        
        # Por usuario
        cursor.execute('''
            SELECT user_role, COUNT(*) 
            FROM conversations 
            GROUP BY user_role
        ''')
        by_role = dict(cursor.fetchall())
        
        conn.close()
        
        return {
            'total_conversations': total,
            'important_conversations': by_importance.get('important', 0) + by_importance.get('critical', 0),
            'trivial_conversations': by_importance.get('trivial', 0),
            'moderate_conversations': by_importance.get('moderate', 0),
            'saved_to_memory': in_memory,
            'manually_marked': manually_marked,
            'by_importance': by_importance,
            'by_role': by_role
        }
    
    def delete_old_conversations(self, days: int = 90):
        """Eliminar conversaciones antiguas (solo triviales)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cutoff_date = datetime.now().timestamp() - (days * 24 * 60 * 60)
        cutoff_iso = datetime.fromtimestamp(cutoff_date).isoformat()
        
        cursor.execute('''
            DELETE FROM conversations
            WHERE timestamp < ?
            AND importance_level = 'trivial'
            AND manually_marked = 0
        ''', (cutoff_iso,))
        
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        
        return deleted
    
    def delete_conversation(self, conversation_id: int):
        """Eliminar una conversación específica"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM conversations WHERE id = ?', (conversation_id,))
        
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted == 0:
            raise ValueError(f"Conversación {conversation_id} no encontrada")
        
        return True
    
    def clear_all_conversations(self):
        """Eliminar TODAS las conversaciones del historial"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM conversations')
        
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        
        print(f"[History] {deleted} conversaciones eliminadas")
        return deleted
    
    def export_important_conversations(self, output_file: str = "important_conversations.json"):
        """Exportar conversaciones importantes a JSON"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM conversations
            WHERE importance_level IN ('important', 'critical')
            OR manually_marked = 1
            ORDER BY timestamp DESC
        ''')
        
        rows = cursor.fetchall()
        conversations = []
        for row in rows:
            conv = dict(row)
            if conv['metadata']:
                conv['metadata'] = json.loads(conv['metadata'])
            conversations.append(conv)
        
        conn.close()
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(conversations, f, indent=2, ensure_ascii=False)
        
        return len(conversations)
