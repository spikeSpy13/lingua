"""Contratos da etapa 09 com dados controlados e embeddings simulados.

Estas verificações não afirmam qualidade semântica nem inferência E5 real.
"""

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
from uuid import UUID

from fixtures_contexto import INSTANTE, PERIODOS, TEXTO
from fixtures_regras import CASOS
from fixtures_vetorizacao import GeradorSimulado, construir_contexto
import vetorizacao as v


def digest(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def digest_json(objeto):
    return digest(json.dumps(objeto, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False))


def ler_vetor(armazenamento):
    dados = base64.b64decode(armazenamento["base64"], validate=True)
    return struct.unpack("<" + "f" * armazenamento["dimensao"], dados)


class BaseVetorizacao:
    @classmethod
    def setUpClass(cls):
        cls.fonte = construir_contexto(atravessar_paragrafos=True)

    def executar(self, *, fonte=None, gerador=None, **opcoes):
        argumentos = {"execucao_id": "vetorizacao-fixture-09", "registrado_em": INSTANTE}
        argumentos.update(opcoes)
        return v.vetorizar_unidades_contexto(self.fonte if fonte is None else fonte,
                                             gerador=gerador or GeradorSimulado(), **argumentos)

    def adulterar(self, mutacao):
        registro = self.executar()
        mutacao(registro)
        with self.assertRaises(v.ErroVetorizacao):
            v.validar_vetorizacao(registro)


class ContratoVetorizacaoTests(BaseVetorizacao, unittest.TestCase):
    def test_textos_dinamicos_nao_exigem_banco_nem_documento_armazenado(self):
        fonte = construir_contexto(pequeno=True)
        registro = self.executar(fonte=fonte)
        self.assertEqual([r["tipo"] for r in registro["representacoes"]],
                         ["foco", "janela", "paragrafo", "documento"])
        self.assertEqual([r["texto"] for r in registro["representacoes"]], ["O estudo."] * 4)
        relatorio = v.validar_vetorizacao(registro)
        self.assertEqual(relatorio["estado"], "valido")
        self.assertFalse(relatorio["pronto_para_uso"])
        self.assertFalse(relatorio["inferencia_real"])

    def test_fonte_integral_preservada_sem_mutacao_e_copia_isolada(self):
        fonte = deepcopy(self.fonte)
        antes = deepcopy(fonte)
        registro = self.executar(fonte=fonte)
        self.assertEqual(fonte, antes)
        self.assertEqual(registro["contexto"], antes)
        self.assertIsNot(registro["contexto"], fonte)
        registro["contexto"]["unidades"][0]["foco"]["texto"] = "adulterado"
        self.assertEqual(fonte, antes)

    def test_tipos_ordem_e_cobertura_com_documento_unico(self):
        registro = self.executar()
        self.assertEqual(len(registro["representacoes"]), 13)
        self.assertEqual([r["ordem"] for r in registro["representacoes"]], list(range(13)))
        self.assertEqual([r["tipo"] for r in registro["representacoes"]],
                         ["foco", "janela"] * 5 + ["paragrafo"] * 2 + ["documento"])
        self.assertEqual(len({r["id"] for r in registro["representacoes"]}), 13)

    def test_foco_janela_e_documento_preservam_campos_exatos_e_intervalos(self):
        registro = self.executar()
        for i, unidade in enumerate(self.fonte["unidades"]):
            for j, tipo in enumerate(("foco", "janela")):
                rep = registro["representacoes"][2 * i + j]
                self.assertEqual(rep["tipo"], tipo)
                self.assertEqual(rep["campo"], tipo + ".texto")
                self.assertEqual(rep["texto"], unidade[tipo]["texto"])
                self.assertEqual(rep["sha256_texto"], unidade[tipo]["sha256_texto"])
                self.assertEqual(rep["trabalho"], unidade[tipo]["trabalho"])
                self.assertEqual(rep["original"], unidade[tipo]["original"])
                self.assertEqual(rep["unidade_id"], unidade["id"])
                self.assertEqual(rep["periodo_foco_id"], unidade["periodo_foco_id"])
        doc = registro["representacoes"][-1]
        self.assertEqual(doc["texto"], TEXTO)
        self.assertEqual(doc["sha256_texto"], digest(TEXTO))
        self.assertEqual(doc["trabalho"], {"inicio": 0, "fim": len(TEXTO)})
        self.assertIsNone(doc["unidade_id"])

    def test_paragrafos_unicos_preservam_unicode_crlf_e_origem(self):
        for normalizar in (False, True):
            fonte = construir_contexto(normalizar=normalizar, atravessar_paragrafos=True)
            registro = self.executar(fonte=fonte, configuracao={"max_tokens": 8})
            segmentacao = fonte["regras"]["analise"]["anotacao"]["segmentacao"]
            paragrafos = [r for r in registro["representacoes"] if r["tipo"] == "paragrafo"]
            self.assertEqual(len(paragrafos), len(segmentacao["paragrafos"]))
            self.assertEqual(len({r["paragrafo_id"] for r in paragrafos}), len(paragrafos))
            for rep, origem in zip(paragrafos, segmentacao["paragrafos"]):
                self.assertEqual(rep["paragrafo_id"], origem["id"])
                self.assertEqual(rep["texto"], origem["texto"])
                self.assertEqual(rep["sha256_texto"], digest(origem["texto"]))
                self.assertEqual(rep["trabalho"], origem["trabalho"])
                self.assertEqual(rep["original"], origem["original"])
                self.assertIsNotNone(rep["vetor"])
            for campo, valor in (("texto", "adulterado"), ("paragrafo_id", "outro")):
                alterado = deepcopy(registro)
                next(r for r in alterado["representacoes"] if r["tipo"] == "paragrafo")[campo] = valor
                with self.assertRaises(v.ErroVetorizacao):
                    v.validar_vetorizacao(alterado)

    def test_resultado_arquivado_1_0_continua_valido(self):
        registro = json.loads((Path(__file__).resolve().parents[1] / "examples" / "vetorizacao_simulada.json").read_text())
        self.assertEqual(registro["schema_version"], "1.0.0")
        self.assertEqual(v.validar_vetorizacao(registro)["estado"], "valido")
        from persistencia_vetores import exportar_zip
        from importacao_vetores import ler_resultado_zip
        self.assertEqual(ler_resultado_zip(exportar_zip(registro), permitir_simulado=True), registro)

    def test_registro_simulado_identifica_natureza_e_backend(self):
        registro = self.executar()
        self.assertEqual(registro["modelo"]["natureza"], "simulado_teste")
        self.assertEqual(registro["modelo"]["identificacao"], "fixture_embeddings")
        self.assertEqual(registro["modelo"]["ambiente"]["backend"], "fixture")
        self.assertEqual(registro["modelo"]["ambiente"]["dispositivo"], "cpu")
        self.assertNotIn("multilingual-e5", registro["modelo"]["identificacao"])

    def test_registro_usa_origem_execucao_e_hash_json_canonico(self):
        registro = self.executar()
        self.assertEqual(registro["contexto_execucao_id"], self.fonte["execucao_id"])
        self.assertEqual(registro["documento_id"], self.fonte["documento_id"])
        self.assertEqual(registro["contexto_sha256"], digest_json(self.fonte))
        self.assertEqual(registro["schema_version"], "1.1.0")
        self.assertEqual(registro["etapa"], "09_vetorizacao")

    def test_ids_datas_da_execucao_nao_mudam_identidade_de_representacao(self):
        a = self.executar()
        b = self.executar(execucao_id="outra-execucao-09", registrado_em="2026-10-09T00:00:00Z")
        for ra, rb in zip(a["representacoes"], b["representacoes"]):
            self.assertNotEqual(ra["id"], rb["id"])
            self.assertEqual(ra["representacao_logica_id"], rb["representacao_logica_id"])
            self.assertEqual(ra["vetor"], rb["vetor"])

    def test_ids_gerados_e_instante_com_fuso(self):
        registro = v.vetorizar_unidades_contexto(self.fonte, gerador=GeradorSimulado())
        UUID(registro["execucao_id"])
        self.assertIsNotNone(datetime.fromisoformat(registro["registrado_em"]).utcoffset())

    def test_json_serializa_e_valida_sem_modelo(self):
        registro = json.loads(json.dumps(self.executar(), ensure_ascii=False, allow_nan=False))
        with patch.object(GeradorSimulado, "gerar", side_effect=AssertionError("Não inferir")), \
                patch.object(GeradorSimulado, "tokenizar", side_effect=AssertionError("Não tokenizar")):
            self.assertEqual(v.validar_vetorizacao(registro)["estado"], "valido")

    def test_consulta_retornada_e_copia_sem_reexecutar_modelo(self):
        registro = self.executar()
        alvo = registro["representacoes"][0]
        encontrado = v.consultar_representacao(registro, alvo["id"])
        self.assertEqual(encontrado, alvo)
        self.assertIsNot(encontrado, alvo)
        encontrado["texto"] = "outra cópia"
        self.assertNotEqual(encontrado, alvo)

    def test_consulta_rejeita_id_inexistente(self):
        with self.assertRaises(v.ErroVetorizacao):
            v.consultar_representacao(self.executar(), "representacao-ausente")


class EntradaVetorizacaoTests(BaseVetorizacao, unittest.TestCase):
    def test_origem_adulterada_e_rejeitada_antes_de_inferencia(self):
        fonte = deepcopy(self.fonte)
        fonte["unidades"][0]["foco"]["texto"] = "mudou"
        gerador = GeradorSimulado()
        with self.assertRaises(v.ErroVetorizacao):
            self.executar(fonte=fonte, gerador=gerador)
        self.assertEqual(gerador.chamadas_geracao, [])

    def test_falso_relatorio_de_prontidao_nao_substitui_revalidacao(self):
        fonte = deepcopy(self.fonte)
        fonte["validacao"]["pronto_para_etapa_09"] = True
        fonte["regras"]["validacao"]["pronto_para_etapa_08"] = False
        with self.assertRaises(v.ErroVetorizacao):
            self.executar(fonte=fonte)

    def test_configuracoes_invalidas_rejeitadas_sem_booleanos_como_inteiros(self):
        for configuracao in ({"max_tokens": True}, {"max_tokens": 7}, {"max_tokens": 513},
                             {"tamanho_lote": True}, {"tamanho_lote": 0},
                             {"agregar": "sim"}, {"perfil": "inventado"},
                             {"campo_desconhecido": True}):
            with self.subTest(configuracao=configuracao), self.assertRaises(v.ErroVetorizacao):
                self.executar(configuracao=configuracao)

    def test_datas_e_identificadores_invalidos(self):
        for opcoes in ({"execucao_id": " "}, {"execucao_id": 4},
                       {"registrado_em": "2026-10-08T12:00:00"}, {"registrado_em": "hoje"}):
            with self.subTest(opcoes=opcoes), self.assertRaises(v.ErroVetorizacao):
                self.executar(**opcoes)

    def test_metadados_do_modelo_exigem_dimensao_e_revisao_imutavel(self):
        for dimensao, revisao in ((True, "a" * 40), (0, "a" * 40), (4, "main")):
            gerador = GeradorSimulado(dimensao=dimensao, revisao=revisao)
            with self.subTest(dimensao=dimensao, revisao=revisao), self.assertRaises(v.ErroVetorizacao):
                self.executar(gerador=gerador)
            self.assertEqual(gerador.chamadas_geracao, [])

    def test_tokenizador_com_ids_booleanos_ou_offsets_invalidos_e_rejeitado(self):
        for caso in ("bool", "offsets", "mascara"):
            gerador = GeradorSimulado()
            original = gerador.tokenizar
            def malformado(texto, prefixo, caso=caso):
                entrada = original(texto, prefixo)
                if caso == "bool":
                    entrada["input_ids"][0] = True
                elif caso == "offsets":
                    entrada["offset_mapping"][2] = [-1, -1]
                else:
                    entrada["special_tokens_mask"].pop()
                return entrada
            with patch.object(gerador, "tokenizar", side_effect=malformado):
                with self.subTest(caso=caso), self.assertRaises(v.ErroVetorizacao):
                    self.executar(gerador=gerador)
            self.assertEqual(gerador.chamadas_geracao, [])

    def test_tokenizador_nao_pode_omitir_todo_conteudo_sem_falhar(self):
        gerador = GeradorSimulado()
        original = gerador.tokenizar
        def omitir(texto, prefixo):
            entrada = original(texto, prefixo)
            return {campo: [valores[0], valores[1], valores[-1]]
                    for campo, valores in entrada.items()}
        with patch.object(gerador, "tokenizar", side_effect=omitir), self.assertRaises(v.ErroInferencia):
            self.executar(gerador=gerador)
        self.assertEqual(gerador.chamadas_geracao, [])

    def test_documento_exclusivamente_espacial_nao_recebe_vetor_artificial(self):
        fonte = construir_contexto(texto=" \t\r\n\r\n ", linhas=[])
        gerador = GeradorSimulado()
        registro = self.executar(fonte=fonte, gerador=gerador)
        doc, = registro["representacoes"]
        self.assertEqual(doc["tipo"], "documento")
        self.assertEqual(doc["construcao"], "sem_conteudo")
        self.assertEqual(doc["texto"], " \t\r\n\r\n ")
        self.assertIsNone(doc["vetor"])
        self.assertEqual(doc["blocos"], [])
        self.assertEqual(gerador.chamadas_geracao, [])

    def test_documento_vazio_e_valido_sem_inferencia(self):
        fonte = construir_contexto(texto="", linhas=[])
        gerador = GeradorSimulado()
        registro = self.executar(fonte=fonte, gerador=gerador)
        self.assertEqual(registro["representacoes"][0]["construcao"], "sem_conteudo")
        self.assertEqual(registro["artefatos"], [])
        self.assertEqual(gerador.chamadas_geracao, [])


class IntegridadeVetorizacaoTests(BaseVetorizacao, unittest.TestCase):
    def test_entrada_tem_prefixo_e_hashes_textuais_e_tokens_independentes(self):
        registro = self.executar()
        for artefato in registro["artefatos"]:
            entrada = artefato["entrada_modelo"]
            self.assertTrue(entrada["texto"].startswith("passage: "))
            self.assertEqual(entrada["sha256_texto"], digest(entrada["texto"]))
            self.assertEqual(entrada["sha256_token_ids"], digest_json(entrada["input_ids"]))
            self.assertEqual(entrada["tokens_total"], len(entrada["input_ids"]))
            corpo = entrada["texto"][len("passage: "):]
            self.assertEqual(entrada["tokens_conteudo"], sum(not c.isspace() for c in corpo))
            self.assertEqual(entrada["tokens_total"], len(corpo) + 3)
            self.assertEqual(len(entrada["offset_mapping"]), entrada["tokens_total"])
            self.assertEqual(len(entrada["special_tokens_mask"]), entrada["tokens_total"])

    def test_armazenamento_float32_le_bytes_hash_dimensao_e_norma(self):
        registro = self.executar()
        for artefato in registro["artefatos"]:
            armazenamento = artefato["armazenamento"]
            bruto = base64.b64decode(armazenamento["base64"], validate=True)
            self.assertEqual(armazenamento["formato"], "float32_le")
            self.assertEqual(armazenamento["dimensao"], 4)
            self.assertEqual(armazenamento["bytes"], 16)
            self.assertEqual(len(bruto), 16)
            self.assertEqual(armazenamento["sha256_bytes"], hashlib.sha256(bruto).hexdigest())
            vetor = ler_vetor(armazenamento)
            self.assertTrue(all(math.isfinite(x) for x in vetor))
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in vetor)), 1.0, places=6)

    def test_adulteracao_da_origem_e_rejeitada_mesmo_com_hash_recalculado(self):
        def mudar(r):
            r["contexto"]["unidades"][0]["foco"]["texto"] = "um texto falso"
            r["contexto"]["unidades"][0]["foco"]["sha256_texto"] = digest("um texto falso")
            r["contexto_sha256"] = digest_json(r["contexto"])
        self.adulterar(mudar)

    def test_adulteracoes_das_representacoes_sao_detectadas(self):
        mutacoes = [
            lambda r: r["representacoes"].pop(),
            lambda r: r["representacoes"].reverse(),
            lambda r: r["representacoes"][0].update(tipo="janela"),
            lambda r: r["representacoes"][0].update(ordem=1),
            lambda r: r["representacoes"][0].update(unidade_id="estrangeira"),
            lambda r: r["representacoes"][0].update(periodo_foco_id="estrangeiro"),
            lambda r: r["representacoes"][1].update(janela_logica_id="0" * 64),
            lambda r: r["representacoes"][0].update(texto="adulterado", sha256_texto=digest("adulterado")),
            lambda r: r["representacoes"][0].update(representacao_logica_id="0" * 64),
            lambda r: r["representacoes"][0]["trabalho"].update(inicio=1),
            lambda r: r["representacoes"][0]["original"].update(fim=1),
            lambda r: r.update(contexto_execucao_id="outra"),
            lambda r: r.update(documento_id="outro"),
            lambda r: r.update(contexto_sha256="0" * 64),
        ]
        for i, mutacao in enumerate(mutacoes):
            with self.subTest(caso=i):
                self.adulterar(mutacao)

    def test_adulteracoes_de_tokens_hashes_armazenamento_e_compatibilidade(self):
        mutacoes = [
            lambda r: r["artefatos"][0]["entrada_modelo"].update(sha256_texto="0" * 64),
            lambda r: r["artefatos"][0]["entrada_modelo"].update(sha256_token_ids="0" * 64),
            lambda r: r["artefatos"][0]["entrada_modelo"].update(tokens_total=1),
            lambda r: r["artefatos"][0]["entrada_modelo"]["input_ids"].append(123),
            lambda r: r["artefatos"][0]["entrada_modelo"]["offset_mapping"].pop(),
            lambda r: r["artefatos"][0]["armazenamento"].update(base64="não é base64"),
            lambda r: r["artefatos"][0]["armazenamento"].update(sha256_bytes="0" * 64),
            lambda r: r["artefatos"][0]["armazenamento"].update(dimensao=True),
            lambda r: r["artefatos"][0]["armazenamento"].update(bytes=8),
            lambda r: r["compatibilidade"].update(sha256="0" * 64),
            lambda r: r["geracao"].update(sha256="0" * 64),
        ]
        for i, mutacao in enumerate(mutacoes):
            with self.subTest(caso=i):
                self.adulterar(mutacao)

    def test_vetores_nan_infinito_zero_e_dimensao_incorreta_sao_rejeitados(self):
        for vetor in ([float("nan"), 0., 0., 1.], [float("inf"), 0., 0., 1.],
                      [0., 0., 0., 0.], [1., 0., 0.]):
            gerador = GeradorSimulado()
            with patch.object(gerador, "gerar", side_effect=lambda lote: [vetor for _ in lote]):
                with self.subTest(vetor=vetor), self.assertRaises(v.ErroVetorizacao):
                    self.executar(gerador=gerador)

    def test_lote_com_quantidade_incorreta_ou_falha_nao_retorna_resultado(self):
        for comportamento in (lambda lote: [], lambda lote: (_ for _ in ()).throw(RuntimeError("falha fixture"))):
            gerador = GeradorSimulado()
            with patch.object(gerador, "gerar", side_effect=comportamento):
                with self.assertRaises(v.ErroVetorizacao):
                    self.executar(gerador=gerador)

    def test_falha_no_segundo_lote_nao_retorna_execucao_parcial(self):
        gerador = GeradorSimulado()
        original = gerador.gerar
        chamadas = []
        def gerar(lote):
            chamadas.append(deepcopy(lote))
            if len(chamadas) == 2:
                raise RuntimeError("Falha controlada no segundo lote")
            return original(lote)
        with patch.object(gerador, "gerar", side_effect=gerar), self.assertRaises(v.ErroVetorizacao):
            self.executar(gerador=gerador, configuracao={"tamanho_lote": 1})
        self.assertEqual(len(chamadas), 2)

    def test_limite_do_gerador_permanece_distinto_de_falha_de_inferencia(self):
        gerador = GeradorSimulado()
        erro = v.ErroLimite("Orçamento do backend excedido")
        with patch.object(gerador, "gerar", side_effect=erro), self.assertRaises(v.ErroLimite) as capturado:
            self.executar(gerador=gerador)
        self.assertIs(capturado.exception, erro)

    def test_limite_do_tokenizador_permanece_distinto_de_falha_de_inferencia(self):
        gerador = GeradorSimulado()
        erro = v.ErroLimite("Orçamento de tokenização excedido")
        with patch.object(gerador, "tokenizar", side_effect=erro), self.assertRaises(v.ErroLimite) as capturado:
            self.executar(gerador=gerador)
        self.assertIs(capturado.exception, erro)
        self.assertEqual(gerador.chamadas_geracao, [])

    def test_json_com_nan_extra_e_rejeitado(self):
        self.adulterar(lambda r: r.update(extra=float("nan")))

    def test_armazenamento_adulterado_com_hash_recalculado_rejeita_nan(self):
        def adulterar(r):
            armazenamento = r["artefatos"][0]["armazenamento"]
            bruto = struct.pack("<ffff", float("nan"), 0., 0., 1.)
            armazenamento["base64"] = base64.b64encode(bruto).decode("ascii")
            armazenamento["sha256_bytes"] = hashlib.sha256(bruto).hexdigest()
            artefato_id = r["artefatos"][0]["id"]
            for rep in r["representacoes"]:
                if rep["construcao"] == "direta" and rep["blocos"][0]["artefato_id"] == artefato_id:
                    rep["vetor"] = deepcopy(armazenamento)
        self.adulterar(adulterar)

    def test_validacao_offline_rejeita_texto_nao_vazio_sem_tokens_de_conteudo(self):
        registro = self.executar()
        for rep in registro["representacoes"]:
            for bloco in rep["blocos"]:
                entrada = bloco["entrada_modelo"]
                for campo in ("input_ids", "offset_mapping", "special_tokens_mask"):
                    valores = entrada[campo]
                    entrada[campo] = [valores[0], valores[1], valores[-1]]
                entrada["sha256_token_ids"] = digest_json(entrada["input_ids"])
                entrada["tokens_total"] = 3
                entrada["tokens_conteudo"] = 0
                bloco["peso_tokens"] = 0
                bloco["artefato_id"] = None
            rep["construcao"] = "sem_conteudo"
            rep["vetor"] = None
            rep["selecao_logica"]["plano"] = [
                {"relativo": b["relativo"], "sha256_texto": b["sha256_texto"],
                 "sha256_entrada": b["entrada_modelo"]["sha256_texto"],
                 "sha256_token_ids": b["entrada_modelo"]["sha256_token_ids"], "peso_tokens": 0}
                for b in rep["blocos"]
            ]
            rep["representacao_logica_id"] = digest_json(rep["selecao_logica"])
        registro["artefatos"] = []
        registro["validacao"]["cobertura"].update(
            artefatos_total=0, gerados=0, reutilizados=0,
            sem_conteudo=len(registro["representacoes"]),
        )
        with self.assertRaises(v.ErroVetorizacao):
            v.validar_vetorizacao(registro)


class TextosLongosVetorizacaoTests(BaseVetorizacao, unittest.TestCase):
    def test_orcamento_binario_inclui_vetores_diretos_de_cada_representacao(self):
        gerador = GeradorSimulado()
        fonte = construir_contexto(pequeno=True)
        # Um artefato de 16 bytes mais três associações com seus próprios
        # payloads de 16 bytes totalizam 64 bytes no contrato portátil.
        with patch.object(v, "MAX_BYTES_VETORES", 32), self.assertRaises(v.ErroLimite):
            self.executar(fonte=fonte, gerador=gerador)
        self.assertEqual(gerador.chamadas_geracao, [])

    def test_limites_de_recursos_falham_antes_de_qualquer_inferencia(self):
        for nome in ("MAX_REPRESENTACOES", "MAX_BLOCOS", "MAX_TOKENS_TOTAL",
                     "MAX_BYTES_VETORES", "MAX_CARACTERES_ENTRADAS"):
            gerador = GeradorSimulado()
            with self.subTest(limite=nome), patch.object(v, nome, 1), self.assertRaises(v.ErroLimite):
                self.executar(gerador=gerador, configuracao={"max_tokens": 8})
            self.assertEqual(gerador.entradas_geradas, [])

    def test_divisao_longa_nao_retokeniza_todo_restante_em_cada_bloco(self):
        from fixtures_regras import ponto, token
        palavra = "Á" * 1024
        texto = palavra + "."
        fonte = construir_contexto(texto=texto,
                                   linhas=[token(palavra, palavra.lower(), "NOUN", 0, "ROOT"), ponto(0)])
        gerador = GeradorSimulado()
        registro = self.executar(fonte=fonte, gerador=gerador, configuracao={"max_tokens": 16})
        self.assertEqual("".join(b["texto"] for b in registro["representacoes"][-1]["blocos"]), texto)
        volume_retokenizado = sum(len(t) for t, _ in gerador.chamadas_tokenizacao)
        self.assertLess(volume_retokenizado, 64 * len(texto))

    def test_prefixo_e_tokens_especiais_contam_no_limite_antes_da_inferencia(self):
        fonte = construir_contexto(pequeno=True)
        gerador = GeradorSimulado()
        registro = self.executar(fonte=fonte, gerador=gerador, configuracao={"max_tokens": 12})
        self.assertTrue(all(r["construcao"] == "direta" for r in registro["representacoes"]))
        self.assertEqual(len(gerador.entradas_geradas), 1)
        self.assertEqual(len(gerador.entradas_geradas[0]["input_ids"]), 12)
        dividido = self.executar(fonte=fonte, configuracao={"max_tokens": 11})
        self.assertTrue(all(r["construcao"] == "agregada" for r in dividido["representacoes"]))

    def test_todos_tipos_largos_sao_divididos_sem_truncamento(self):
        gerador = GeradorSimulado()
        registro = self.executar(gerador=gerador, configuracao={"max_tokens": 8, "tamanho_lote": 2})
        self.assertEqual({r["tipo"] for r in registro["representacoes"]
                          if r["construcao"] == "agregada"}, {"foco", "janela", "paragrafo", "documento"})
        for lote in gerador.chamadas_geracao:
            self.assertLessEqual(len(lote), 2)
            for entrada in lote:
                self.assertLessEqual(len(entrada["input_ids"]), 8)
        for rep in registro["representacoes"]:
            self.assertEqual("".join(b["texto"] for b in rep["blocos"]), rep["texto"])

    def test_blocos_contiguos_unicode_crlf_intervalos_hashes_e_pesos(self):
        caso = CASOS["unicode_crlf"]
        fonte = construir_contexto(texto=caso["texto"], linhas=caso["tokens"],
                                   atravessar_paragrafos=True)
        registro = self.executar(fonte=fonte, configuracao={"max_tokens": 8})
        for rep in registro["representacoes"]:
            fim = 0
            for i, bloco in enumerate(rep["blocos"]):
                self.assertEqual(bloco["ordem"], i)
                self.assertEqual(bloco["relativo"]["inicio"], fim)
                fim = bloco["relativo"]["fim"]
                self.assertEqual(bloco["texto"], rep["texto"][bloco["relativo"]["inicio"]:fim])
                self.assertEqual(bloco["sha256_texto"], digest(bloco["texto"]))
                self.assertEqual(bloco["trabalho"]["inicio"],
                                 rep["trabalho"]["inicio"] + bloco["relativo"]["inicio"])
                self.assertEqual(bloco["trabalho"]["fim"], rep["trabalho"]["inicio"] + fim)
                self.assertEqual(bloco["peso_tokens"], sum(not c.isspace() for c in bloco["texto"]))
            self.assertEqual(fim, len(rep["texto"]))
        self.assertEqual(registro["representacoes"][-1]["texto"], caso["texto"])

    def test_agregacao_e_media_ponderada_l2_com_vetores_dos_blocos_preservados(self):
        registro = self.executar(configuracao={"max_tokens": 8})
        artefatos = {a["id"]: a for a in registro["artefatos"]}
        for rep in registro["representacoes"]:
            if rep["construcao"] != "agregada":
                continue
            pesos = [b["peso_tokens"] for b in rep["blocos"]]
            vetores = [ler_vetor(artefatos[b["artefato_id"]]["armazenamento"]) for b in rep["blocos"]]
            media = [sum(p * vetor[j] for p, vetor in zip(pesos, vetores)) / sum(pesos)
                     for j in range(4)]
            norma = math.sqrt(sum(x * x for x in media))
            esperado = [x / norma for x in media]
            atual = ler_vetor(rep["vetor"])
            for a, e in zip(atual, esperado):
                self.assertAlmostEqual(a, e, places=6)

    def test_agregacao_desabilitada_preserva_blocos_sem_vetor_resumo(self):
        registro = self.executar(configuracao={"max_tokens": 8, "agregar": False})
        self.assertTrue(any(r["construcao"] == "blocos" for r in registro["representacoes"]))
        for rep in registro["representacoes"]:
            if rep["construcao"] == "blocos":
                self.assertIsNone(rep["vetor"])
                self.assertGreater(len(rep["blocos"]), 1)
                self.assertTrue(all(b["artefato_id"] for b in rep["blocos"]))

    def test_blocos_opostos_com_pesos_iguais_falham_na_agregacao_sem_vetor_zero(self):
        fonte = construir_contexto(pequeno=True)
        gerador = GeradorSimulado(dimensao=2)
        # O estudo. divide em O est (quatro tokens não espaciais) e udo.
        # (quatro tokens). A média de [1, 0] e [-1, 0] não tem norma L2.
        with patch.object(gerador, "gerar", return_value=[[1., 0.], [-1., 0.]]), \
                self.assertRaises(v.ErroInferencia):
            self.executar(fonte=fonte, gerador=gerador, configuracao={"max_tokens": 8})

    def test_adulteracoes_de_cobertura_peso_e_artefato_sao_detectadas(self):
        registro = self.executar(configuracao={"max_tokens": 8})
        for i, mutacao in enumerate((lambda r: r["representacoes"][0]["blocos"].pop(),
                                    lambda r: r["representacoes"][0]["blocos"].reverse(),
                                    lambda r: r["representacoes"][0]["blocos"][0].update(peso_tokens=999),
                                    lambda r: r["representacoes"][0]["blocos"][0].update(artefato_id="inexistente"),
                                    lambda r: r["representacoes"][0]["blocos"][0]["relativo"].update(fim=1))):
            alterado = deepcopy(registro)
            mutacao(alterado)
            with self.subTest(caso=i), self.assertRaises(v.ErroVetorizacao):
                v.validar_vetorizacao(alterado)


class ReutilizacaoCompatibilidadeTests(BaseVetorizacao, unittest.TestCase):
    def test_mesma_entrada_foco_janela_documento_calcula_uma_vez(self):
        gerador = GeradorSimulado()
        registro = self.executar(fonte=construir_contexto(pequeno=True), gerador=gerador)
        self.assertEqual(len(gerador.entradas_geradas), 1)
        self.assertEqual(len(registro["artefatos"]), 1)
        self.assertEqual(len(registro["representacoes"]), 4)
        self.assertEqual(len({r["representacao_logica_id"] for r in registro["representacoes"]}), 4)

    def test_cache_entre_execucoes_preserva_associacoes_sem_nova_inferencia(self):
        primeiro = self.executar()
        cache = {a["id"]: deepcopy(a) for a in primeiro["artefatos"]}
        gerador = GeradorSimulado()
        segundo = self.executar(gerador=gerador, cache=cache, execucao_id="segunda-09")
        self.assertEqual(gerador.entradas_geradas, [])
        for a, b in zip(primeiro["artefatos"], segundo["artefatos"]):
            self.assertEqual(a["id"], b["id"])
            self.assertEqual(a["entrada_modelo"], b["entrada_modelo"])
            self.assertEqual(a["armazenamento"], b["armazenamento"])
            self.assertEqual(a["origem_calculo"], "gerado")
            self.assertEqual(b["origem_calculo"], "reutilizado")
        self.assertNotEqual(primeiro["representacoes"][0]["id"], segundo["representacoes"][0]["id"])

    def test_cache_corrompido_e_rejeitado_sem_inferencia_silenciosa(self):
        registro = self.executar()
        cache = {a["id"]: deepcopy(a) for a in registro["artefatos"]}
        chave = next(iter(cache))
        cache[chave]["armazenamento"]["base64"] = "inválido"
        gerador = GeradorSimulado()
        with self.assertRaises(v.ErroVetorizacao):
            self.executar(cache=cache, gerador=gerador)
        self.assertEqual(gerador.entradas_geradas, [])

    def test_revisao_prefixo_precisao_e_limite_nao_reutilizam_cache_incompativel(self):
        registro = self.executar()
        cache = {a["id"]: deepcopy(a) for a in registro["artefatos"]}
        variantes = [(GeradorSimulado(revisao="b" * 40), {}),
                     (GeradorSimulado(), {"perfil": "similaridade"}),
                     (GeradorSimulado(precisao="float16"), {}),
                     (GeradorSimulado(), {"max_tokens": 511})]
        for gerador, configuracao in variantes:
            with self.subTest(configuracao=configuracao, revisao=gerador.revisao,
                              precisao=gerador.precisao):
                self.executar(gerador=gerador, cache=cache, configuracao=configuracao)
                self.assertGreater(len(gerador.entradas_geradas), 0)

    def test_normalizacao_crlf_muda_texto_vetor_sem_confundir_identidade_janela(self):
        literal = construir_contexto(atravessar_paragrafos=True)
        normalizada = construir_contexto(normalizar=True, atravessar_paragrafos=True)
        a, b = self.executar(fonte=literal), self.executar(fonte=normalizada)
        ua, ub = literal["unidades"][2], normalizada["unidades"][2]
        self.assertEqual(ua["janela_logica_id"], ub["janela_logica_id"])
        ra, rb = a["representacoes"][5], b["representacoes"][5]
        self.assertNotEqual(ra["sha256_texto"], rb["sha256_texto"])
        self.assertNotEqual(ra["representacao_logica_id"], rb["representacao_logica_id"])
        self.assertNotEqual(ra["vetor"], rb["vetor"])
        self.assertEqual(ra["original"], rb["original"])

    def test_perfis_documento_consulta_e_similaridade_preservam_prefixos(self):
        for perfil, prefixo in (("recuperacao", "passage: "), ("consulta", "query: "),
                                ("similaridade", "query: ")):
            registro = self.executar(configuracao={"perfil": perfil})
            with self.subTest(perfil=perfil):
                self.assertEqual(registro["configuracao"]["perfil"], perfil)
                self.assertTrue(all(a["entrada_modelo"]["texto"].startswith(prefixo)
                                    for a in registro["artefatos"]))

    def test_compatibilidade_verificada_por_perfil_nao_so_dimensao(self):
        registro = self.executar()
        resultado = v.comparar_compatibilidade(registro, registro["compatibilidade"])
        self.assertTrue(resultado["compativel"])
        outro = self.executar(gerador=GeradorSimulado(revisao="b" * 40))
        self.assertEqual(registro["modelo"]["dimensao"], outro["modelo"]["dimensao"])
        self.assertFalse(v.comparar_compatibilidade(registro, outro["compatibilidade"])["compativel"])

    def test_compatibilidade_rejeita_descricao_com_checksum_adulterado(self):
        registro = self.executar()
        perfil = deepcopy(registro["compatibilidade"])
        perfil["sha256"] = "0" * 64
        with self.assertRaises(v.ErroVetorizacao):
            v.comparar_compatibilidade(registro, perfil)

    def test_tamanho_lote_muda_metadados_mas_nao_vetores(self):
        a = self.executar(configuracao={"tamanho_lote": 1})
        b = self.executar(configuracao={"tamanho_lote": 3})
        for ra, rb in zip(a["representacoes"], b["representacoes"]):
            self.assertEqual(ra["vetor"], rb["vetor"])


class ExemploVetorizacaoTests(unittest.TestCase):
    def test_exemplo_e_json_independente_valido_explicitamente_simulado(self):
        arquivo = Path(__file__).resolve().parents[1] / "examples/vetorizacao_simulada.json"
        registro = json.loads(arquivo.read_text(encoding="utf-8"))
        self.assertEqual(registro["modelo"]["natureza"], "simulado_teste")
        relatorio = v.validar_vetorizacao(registro)
        self.assertEqual(relatorio["estado"], "valido")
        self.assertFalse(relatorio["pronto_para_uso"])


if __name__ == "__main__":
    unittest.main()
