"""Credenciais de cada busca permanecem separadas do ambiente e dos resultados."""

import json
import os
import unittest
from unittest.mock import Mock, patch

import api_narrativas as api
from agente_analista.app import ServicoAnalista
from agente_analista.entrada import validar_relato
from agente_analista import ligacoes


class CredenciaisTransporteTests(unittest.TestCase):
    def setUp(self):
        self.ambiente = patch.dict(os.environ, {}, clear=True)
        self.ambiente.start()
        self.addCleanup(self.ambiente.stop)
        self.cliente = Mock()
        resposta = Mock()
        resposta.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"ligacoes":[]}'}, "finish_reason": "stop"}],
        }).encode("utf-8")
        self.cliente.open.return_value.__enter__ = Mock(return_value=resposta)
        self.cliente.open.return_value.__exit__ = Mock(return_value=False)
        self.opener = patch("api_narrativas.build_opener", return_value=self.cliente)
        self.opener.start()
        self.addCleanup(self.opener.stop)
        self.certificados = patch("api_narrativas._contexto_https")
        self.certificados.start()
        self.addCleanup(self.certificados.stop)
        self.relato = validar_relato("A lembrança retorna.\n\nPenso na casa.")
        fonte = "A lembrança retorna no sonho."
        self.recuperacao = {
            "consultas": [{"id": "Q1", "origem": "P1", "texto": "A lembrança retorna."}],
            "candidatos": [{"bloco_id": "B1", "texto": fonte,
                "fragmentos_ids": ["F1"], "fragmentos": [{"id": "F1", "inicio": 0, "fim": len(fonte)}],
                "referencia": {"obra": "Fonte real", "volume": 1, "paginas": [1], "secao": None},
                "pontuacoes": [], "rrf": 0.02}],
            "metodo_fusao": {"nome": "RRF", "k": 60},
        }

    def pedido_enviado(self):
        return self.cliente.open.call_args.args[0]

    def enviar(self, **credenciais):
        return api._enviar_mensagens(provedor="openrouter", modelo="fabricante/modelo-teste",
            construtor=lambda: [{"role": "user", "content": "Contexto de teste."}],
            contexto={}, **credenciais)

    def test_avaliacao_env_sem_chave_usa_chave_e_modelo_da_busca(self):
        for provedor, modelo, destino in (
            ("openrouter", "fabricante/modelo-escolhido", "https://openrouter.ai/api/v1/chat/completions"),
            ("openai", "modelo-escolhido", "https://api.openai.com/v1/chat/completions"),
        ):
            with self.subTest(provedor=provedor):
                resultado = ligacoes.avaliar_ligacoes(self.relato, self.recuperacao,
                    provedor=provedor, modelo=modelo, chave_api="credencial-da-busca")
                pedido = self.pedido_enviado()
                corpo = json.loads(pedido.data)
                self.assertEqual(pedido.full_url, destino)
                self.assertEqual(pedido.get_header("Authorization"), "Bearer credencial-da-busca")
                self.assertEqual(corpo["model"], modelo)
                self.assertEqual(corpo["response_format"]["type"], "json_schema")
                self.assertNotIn("credencial-da-busca", pedido.data.decode("utf-8"))
                self.assertNotIn("credencial-da-busca", json.dumps(resultado))
                self.assertNotIn("NARRATIVA_API_KEY", os.environ)

    def test_credencial_e_modelo_explicitos_nao_usam_fallback_do_ambiente(self):
        os.environ.update(NARRATIVA_API_KEY="credencial-servidor", AGENTE_ANALISTA_PROVEDOR="openai",
                          AGENTE_ANALISTA_MODELO="modelo-servidor")
        antes = os.environ.copy()
        ligacoes.avaliar_ligacoes(self.relato, self.recuperacao, provedor="openrouter",
                                 modelo="fabricante/modelo-pagina", chave_api="credencial-pagina")
        pedido = self.pedido_enviado()
        self.assertEqual(pedido.get_header("Authorization"), "Bearer credencial-pagina")
        self.assertEqual(json.loads(pedido.data)["model"], "fabricante/modelo-pagina")
        self.assertEqual(pedido.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(os.environ.copy(), antes)

    def test_modelo_gratuito_catalogo_texto_preserva_modelo_chave_e_uma_chamada(self):
        modelo = "nvidia/nemotron-3-ultra-550b-a55b:free"
        servico = ServicoAnalista()
        servico._corpus = Mock(return_value=object())
        servico.buscador = Mock()
        servico.buscador.buscar.return_value = self.recuperacao
        with patch("agente_analista.modelos.escolher_formato_resposta", return_value="texto") as capacidades:
            resultado = servico.executar(self.relato, Mock(), provedor="openrouter", modelo=modelo,
                                         chave_api="credencial-pagina")
        capacidades.assert_called_once_with("openrouter", modelo)
        self.cliente.open.assert_called_once()
        pedido = self.pedido_enviado()
        corpo = json.loads(pedido.data)
        self.assertEqual(corpo["model"], modelo)
        self.assertEqual(pedido.get_header("Authorization"), "Bearer credencial-pagina")
        self.assertNotIn("response_format", corpo)
        self.assertNotIn("provider", corpo)
        self.assertNotIn("n", corpo)
        self.assertEqual(resultado["avaliacao"]["bytes_pedido"], len(pedido.data))
        self.assertEqual(resultado["justificativas"], {
            "provedor": "openrouter", "modelo": modelo, "formato_resposta": "texto"})
        self.assertNotIn("credencial-pagina", json.dumps(resultado))
        self.assertNotIn("NARRATIVA_API_KEY", os.environ)

    def test_chave_none_preserva_compatibilidade_do_transporte_legado(self):
        os.environ["NARRATIVA_API_KEY"] = "credencial-legada"
        for extras in ({}, {"chave_api": None}):
            with self.subTest(extras=extras):
                self.assertEqual(self.enviar(**extras), '{"ligacoes":[]}')
                self.assertEqual(self.pedido_enviado().get_header("Authorization"), "Bearer credencial-legada")

    def test_chave_vazia_explicita_nao_recupera_chave_do_ambiente(self):
        os.environ["NARRATIVA_API_KEY"] = "credencial-ambiente-nao-usar"
        for valor in ("", "   "):
            with self.subTest(valor=valor), self.assertRaises(api.ErroAPINarrativa) as erro:
                self.enviar(chave_api=valor)
            self.assertNotIn("credencial-ambiente-nao-usar", str(erro.exception))
        self.cliente.open.assert_not_called()

    def test_chave_invalida_nao_envia_pedido_nem_revela_credencial(self):
        for valor in ("não-ascii", "chave com espaços", "chave\r\nHeader:valor", "chave\x7f", 42, []):
            with self.subTest(valor=valor), self.assertRaises(api.ErroAPINarrativa) as erro:
                self.enviar(chave_api=valor)
            if isinstance(valor, str):
                self.assertNotIn(valor, str(erro.exception))
        self.cliente.open.assert_not_called()

    def test_avaliacao_chave_vazia_explicita_nao_recupera_chave_do_ambiente(self):
        os.environ["NARRATIVA_API_KEY"] = "credencial-ambiente-nao-usar"
        with self.assertRaises(ligacoes.ErroLigacoes) as erro:
            ligacoes.avaliar_ligacoes(self.relato, self.recuperacao, provedor="openrouter",
                                     modelo="fabricante/modelo-pagina", chave_api="")
        self.assertNotIn("credencial-ambiente-nao-usar", str(erro.exception))
        self.cliente.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
