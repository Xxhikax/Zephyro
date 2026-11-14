"""Memory router — history, knowledge graph, reminders endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth_middleware import require_auth, require_admin

router = APIRouter()


def get_orchestrator(request: Request):
    return request.app.state.orchestrator


# ============ HISTORY ============

@router.get("/history/list")
async def get_conversation_history(
    request: Request,
    session: dict = Depends(require_admin),
    limit: int = 50,
    importance_level: str = None
):
    """Obtener historial de conversaciones agrupadas (solo admin)"""
    orchestrator = get_orchestrator(request)

    try:
        conversations = orchestrator.history.get_grouped_conversations(limit=limit)

        if importance_level:
            conversations = [c for c in conversations if c['importance_level'] == importance_level]

        normalized_conversations = []
        for conv in conversations:
            normalized_conversations.append({
                'id': conv.get('id'),
                'session_id': conv.get('session_id'),
                'title': conv.get('conversation_title', 'Sin título'),
                'created_at': conv.get('timestamp', ''),
                'message_count': conv.get('message_count', 1),
                'messages': conv.get('messages', []),
                'importance_level': conv.get('importance_level', 'trivial'),
                'saved_to_memory': conv.get('saved_to_memory', False)
            })

        total = len(normalized_conversations)
        important_count = sum(1 for c in normalized_conversations if c['importance_level'] in ['important', 'critical'])

        return {
            "success": True,
            "conversations": normalized_conversations,
            "count": len(normalized_conversations),
            "total": total,
            "important_count": important_count
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/history/conversation/{conversation_id}")
async def get_conversation(
    request: Request,
    conversation_id: int,
    session: dict = Depends(require_admin),
):
    """Obtener conversación específica"""
    orchestrator = get_orchestrator(request)

    try:
        if not orchestrator.history:
            return {"success": False, "error": "Historial no disponible"}
        conversation = orchestrator.history.get_conversation(conversation_id)
        if conversation:
            return {"success": True, "conversation": conversation}
        return {"success": False, "error": "Conversación no encontrada"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/history/mark-important")
async def mark_conversation_important(
    request: Request,
    conversation_id: int,
    session: dict = Depends(require_admin),
):
    """Marcar conversación como importante"""
    orchestrator = get_orchestrator(request)

    try:
        orchestrator.mark_conversation_important(conversation_id)
        return {"success": True, "message": f"Conversación {conversation_id} marcada como importante"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/history/statistics")
async def get_history_statistics(
    request: Request,
    session: dict = Depends(require_admin),
):
    """Obtener estadísticas del historial"""
    orchestrator = get_orchestrator(request)

    try:
        if not orchestrator.history:
            return {"success": False, "error": "Historial no disponible"}
        stats = orchestrator.history.get_statistics()
        return {"success": True, "statistics": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/history/search")
async def search_conversations(
    request: Request,
    query: str,
    limit: int = 20,
    session: dict = Depends(require_admin),
):
    """Buscar en el historial"""
    orchestrator = get_orchestrator(request)

    try:
        if not orchestrator.history:
            return {"success": False, "error": "Historial no disponible"}
        results = orchestrator.history.search_conversations(query, limit=limit)
        return {"success": True, "results": results, "count": len(results)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/history/delete/{conversation_id}")
async def delete_conversation(
    request: Request,
    conversation_id: int,
    session: dict = Depends(require_admin),
):
    """Eliminar una conversación del historial"""
    orchestrator = get_orchestrator(request)

    try:
        if not orchestrator.history:
            return {"success": False, "error": "Historial no disponible"}
        orchestrator.history.delete_conversation(conversation_id)
        return {"success": True, "message": "Conversación eliminada correctamente"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/history/clear")
async def clear_all_history(
    request: Request,
    session: dict = Depends(require_admin),
):
    """Eliminar todo el historial de conversaciones"""
    orchestrator = get_orchestrator(request)

    try:
        if not orchestrator.history:
            return {"success": False, "error": "Historial no disponible"}
        orchestrator.history.clear_all_conversations()
        return {"success": True, "message": "Todo el historial ha sido eliminado"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/session/reset")
async def reset_session(
    request: Request,
    session: dict = Depends(require_auth),
):
    """Reset de 'nueva conversación': limpia el contexto de corto plazo en RAM
    (historial + cross-turn buffer) sin borrar la memoria de largo plazo ni el
    historial persistido en SQLite. Previene contaminación cross-sesión cuando el
    frontend reusa el mismo token."""
    orchestrator = get_orchestrator(request)
    try:
        orchestrator.reset_conversation()
        return {"success": True, "message": "Contexto de conversación reiniciado"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============ KNOWLEDGE GRAPH ============

@router.get("/kg/stats")
async def kg_stats(
    request: Request,
    session: dict = Depends(require_auth),
):
    """Estadísticas del Knowledge Graph"""
    orchestrator = get_orchestrator(request)
    if not orchestrator.knowledge_graph:
        return {"error": "Knowledge Graph no disponible"}
    return {"stats": orchestrator.knowledge_graph.get_stats()}


@router.get("/kg/export")
async def kg_export(
    request: Request,
    session: dict = Depends(require_auth),
):
    """Exporta el Knowledge Graph para visualización"""
    orchestrator = get_orchestrator(request)
    if not orchestrator.knowledge_graph:
        return {"error": "Knowledge Graph no disponible"}
    return orchestrator.knowledge_graph.export_json()


@router.post("/kg/query")
async def kg_query(
    request: Request,
    session: dict = Depends(require_auth),
):
    """Consulta el Knowledge Graph"""
    orchestrator = get_orchestrator(request)
    data = await request.json()
    question = data.get("question", "")
    if not orchestrator.knowledge_graph:
        return {"error": "Knowledge Graph no disponible"}
    result = orchestrator.knowledge_graph.query(question)
    return {"result": result}


# ============ REMINDERS ============

@router.get("/reminders/list")
async def list_reminders(
    request: Request,
    session: dict = Depends(require_auth),
):
    """Lista los recordatorios activos"""
    orchestrator = get_orchestrator(request)
    if not orchestrator.reminder_system:
        return {"error": "Sistema de recordatorios no disponible"}
    user_role = session.get("role", "guest")
    result = orchestrator.reminder_system.list_reminders(user_role)
    return {"reminders": result}


@router.post("/reminders/create")
async def create_reminder(
    request: Request,
    session: dict = Depends(require_auth),
):
    """Crea un nuevo recordatorio"""
    orchestrator = get_orchestrator(request)
    data = await request.json()
    message = data.get("message", "")
    when_str = data.get("when", "")
    if not message or not when_str:
        raise HTTPException(status_code=400, detail="message and when are required")
    if not orchestrator.reminder_system:
        return {"error": "Sistema de recordatorios no disponible"}
    user_role = session.get("role", "guest")
    reminder_id, feedback = orchestrator.reminder_system.create_reminder(message, when_str, user_role)
    return {"id": reminder_id, "message": feedback}


@router.delete("/reminders/{reminder_id}")
async def cancel_reminder(
    request: Request,
    reminder_id: int,
    session: dict = Depends(require_auth),
):
    """Cancela un recordatorio"""
    orchestrator = get_orchestrator(request)
    if not orchestrator.reminder_system:
        return {"error": "Sistema de recordatorios no disponible"}
    result = orchestrator.reminder_system.cancel_reminder(reminder_id)
    return {"message": result}
