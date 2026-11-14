#!/usr/bin/env python3
"""
Tests para la funcionalidad mejorada de Playwright.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock
from modules.automation.planner import WebAutomationPlanner
from modules.automation.browser_controller import BrowserController
from modules.automation.app_controller import AppController

class TestWebAutomationPlanner(unittest.TestCase):
    """Tests para el planificador de automatización web"""
    
    def setUp(self):
        self.planner = WebAutomationPlanner()
    
    def test_build_plan_play_media_youtube(self):
        """Plan para reproducir en YouTube"""
        analysis = {
            "automation_intent": "PLAY_MEDIA",
            "provider": "youtube",
            "search_query": "lo-fi beats"
        }
        plan = self.planner.build_plan(analysis)
        self.assertGreater(len(plan), 0)
        self.assertEqual(plan[0]["type"], "navigate")
        self.assertEqual(plan[0]["provider"], "youtube")
    
    def test_build_plan_search(self):
        """Plan para búsqueda genérica"""
        analysis = {
            "automation_intent": "SEARCH",
            "search_query": "tutorial de Python"
        }
        plan = self.planner.build_plan(analysis)
        self.assertGreater(len(plan), 0)
        self.assertEqual(plan[0]["type"], "navigate")
    
    def test_build_plan_navigate(self):
        """Plan para navegar a URL"""
        analysis = {
            "automation_intent": "NAVIGATE",
            "target": "youtube"
        }
        plan = self.planner.build_plan(analysis)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["type"], "navigate")
        self.assertIn("youtube.com", plan[0]["params"]["url"])
    
    def test_resolve_url(self):
        """Conversión de nombres de sitios a URLs"""
        self.assertEqual(
            self.planner._resolve_url("youtube"),
            "https://www.youtube.com"
        )
        self.assertEqual(
            self.planner._resolve_url("instagram"),
            "https://www.instagram.com"
        )
        self.assertEqual(
            self.planner._resolve_url("https://example.com"),
            "https://example.com"
        )
        self.assertTrue(
            self.planner._resolve_url("búsqueda cualquiera").startswith("https://duckduckgo.com")
        )

class TestBrowserController(unittest.TestCase):
    """Tests para el controlador de navegador (mocked)"""
    
    def setUp(self):
        self.browser = BrowserController(headless=True)
        # Mockear métodos async que dependen de Playwright
        # self.browser._run_async removed (no longer exists)
    
    @patch('modules.automation.browser_controller.sync_playwright')
    def test_init(self, mock_playwright):
        """Inicialización con parámetros"""
        browser = BrowserController(headless=False, screenshot_retention_days=3, max_screenshot_files=10)
        self.assertEqual(browser.headless, False)
        self.assertEqual(browser.screenshot_retention_days, 3)
        self.assertEqual(browser.max_screenshot_files, 10)
    
    def test_public_methods_exist(self):
        """Verificar que los métodos públicos nuevos existen"""
        self.assertTrue(hasattr(self.browser, 'play_media'))
        self.assertTrue(hasattr(self.browser, 'control_media'))
        self.assertTrue(hasattr(self.browser, 'wait_for'))
        self.assertTrue(hasattr(self.browser, 'find_by_text'))
        self.assertTrue(hasattr(self.browser, 'close_tab'))

class TestAppControllerIntegration(IsolatedAsyncioTestCase):
    """Tests de integración del controlador de apps"""
    
    def setUp(self):
        self.controller = AppController(headless=True)
        # Mockear el browser para no lanzar navegador real
        self.controller.browser = AsyncMock()
        # Configurar todos los métodos que puede llamar _execute_atomic_action
        self.controller.browser.navigate_to = AsyncMock(return_value="✓ Navegado")
        self.controller.browser.open_and_search = AsyncMock(return_value="✓ Buscado")
        self.controller.browser.click = AsyncMock(return_value="✓ Click")
        self.controller.browser.click_element = AsyncMock(return_value="✓ Click elemento")
        self.controller.browser.type_text = AsyncMock(return_value="✓ Escrito")
        self.controller.browser.wait_for = AsyncMock(return_value="✓ Esperado")
        self.controller.browser.play_media = AsyncMock(return_value="✓ Reproduciendo")
        self.controller.browser.control_media = AsyncMock(return_value="✓ Controlado")
        self.controller.browser.find_by_text = AsyncMock(return_value="✓ Encontrado")
        self.controller.browser.close_tab = AsyncMock(return_value="✓ Pestaña cerrada")
        self.controller.browser.scroll = AsyncMock(return_value="✓ Scroll")
        self.controller.browser.get_text = AsyncMock(return_value="✓ Texto")
        self.controller.browser.screenshot = AsyncMock(return_value="✓ Captura")
    
    async def test_execute_with_automation_intent(self):
        """Ejecución con automation_intent usa planner"""
        analysis = {
            "automation_intent": "PLAY_MEDIA",
            "provider": "youtube",
            "search_query": "música relajante"
        }
        result = await self.controller.execute(analysis)
        # El resultado debe ser un string, no un Mock
        self.assertIsInstance(result, str)
        # Puede ser éxito o fallo, pero no error
        self.assertTrue(result.startswith("✓") or result.startswith("✗"))
    
    async def test_execute_close_tab(self):
        """Ejecución de cierre de pestaña"""
        analysis = {
            "automation_intent": "INTERACT",
            "action": "close_tab",
            "provider": "generic"
        }
        result = await self.controller.execute(analysis)
        self.assertIsInstance(result, str)
        self.controller.browser.close_tab.assert_called_once()

if __name__ == '__main__':
    unittest.main(verbosity=2)