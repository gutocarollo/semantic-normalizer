"""Bare tokens that are also ordinary Portuguese words must not match automatically.

Four review rounds converged on one root cause, and this file is that cause turned into a
gate. Portuguese uses the same token for a financial noun and an everyday word, and a bare
automatic form cannot tell them apart:

    opções     financial options          / alternatives      67 % precision before the fix
    futuros    futures contracts          / future (adj.)     80 %
    desconto   discount on face value     / a fee rebate      83 %
    rendimento yield                      / taxable income    83 %
    valores    securities                 / amounts, scores   0 % outside its compounds
    IR         Information Ratio          / Imposto de Renda  57 %

The repair is always the same and it is not suppression: the corpus marks the financial
sense with a collocation — `contratos futuros`, `opção de compra`, `bolsa de valores` — so the
phrase becomes the automatic anchor and the bare token drops to `review`.

These cases were measured by exhaustive sweep of every corpus occurrence, never by sample.
Sampling reported 95-100 % precision on batches an exhaustive sweep scored at 84 %.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location("build_oov_queue", ROOT / "scripts" / "build_oov_queue.py")
assert _spec and _spec.loader
_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_builder)

from semantic_normalizer.normalizer import normalize_text  # noqa: E402
from semantic_normalizer.registry import load_lexicon  # noqa: E402

# (sentence, concept, must the concept appear?) — every sentence is real corpus text or a
# minimal paraphrase of one, and every False was a confirmed false positive.
CASES = [
    ("Os títulos dos EUA são uma das opções mais seguras.", "technical.option", False),
    ("Entre duas opções, devemos escolher aquela que possui o maior valor.", "technical.option", False),
    ("Ao embutir opções de compra a títulos, esperamos o seguinte.", "technical.option", True),
    ("O título possui uma opção de venda ao emissor.", "technical.option", True),
    ("Expectativas sobre os comportamentos dos juros para períodos futuros.", "technical.futures", False),
    ("Garantia de resultados futuros ou isenção de risco para o investidor.", "technical.futures", False),
    ("Rafael comprou 10 contratos futuros de Ibovespa a 60.000.", "technical.futures", True),
    ("O acordo resulte em desconto, abatimento ou redução de taxa de administração.", "technical.discount", False),
    ("O título tem um deságio sobre o valor de face.", "technical.discount", True),
    ("A operação que deu origem ao rendimento para o cotista.", "technical.yield", False),
    # `Yield to Worst` is its own concept, so longest-match takes the whole term and the
    # generic yield concept correctly stays out. Asserting both sides keeps that guaranteed.
    ("O Yield to Worst é o rendimento no pior cenário.", "technical.yield_to_worst", True),
    ("O Yield to Worst é o rendimento no pior cenário.", "technical.yield", False),
    ("O Current Yield divide o cupom anual pelo preço de mercado.", "technical.current_yield", True),
    ("O Oscilador Estocástico gera valores entre 0 e 100.", "entity.securities", False),
    ("A Bolsa de Valores oferece maior transparência.", "entity.securities", True),
    ("A CVM regula os valores mobiliários.", "entity.securities", True),
    ("Os CDs comprado por meio de um banco segurado possui proteção.", "technical.long_position", False),
    ("O gestor está com posição comprada em dólar.", "technical.long_position", True),
    ("O órgão busca a proteção do investidor por meio da regulação.", "technical.hedge", False),
    ("O gestor faz hedge cambial da carteira com contratos futuros.", "technical.hedge", True),
    ("A taxa ESTR é calculada pelo Banco Central Europeu.", "technical.bacen", False),
    ("O BACEN define a taxa Selic.", "technical.bacen", True),
    ("Há ajustes diários de margem nos contratos futuros.", "artifact.configuration", False),
    # Fixed expressions that merely contain a label, blocked by `forbidden_variants`.
    ("É vedada a vinculação, a qualquer título, de parcela do patrimônio.", "entity.security", False),
    ("Dentre os títulos americanos, temos T-Bills e T-Notes.", "entity.security", True),
    ("Expandimos nossa metodologia de ensino de alta performance.", "quantity.performance", False),
    ("A taxa de performance incide sobre os ganhos do fundo.", "quantity.performance", True),
    # Same rule: the specific concept must win over the quantity it is built from.
    ("A macro atribuição de performance é feita no nível do patrocinador.", "technical.performance_attribution", True),
    ("A macro atribuição de performance é feita no nível do patrocinador.", "quantity.performance", False),
    ("Realizando todas as ações necessárias para tal exercício.", "entity.share", False),
    ("As ações são negociadas publicamente na bolsa de valores.", "entity.share", True),
    # An English compound the material quotes verbatim must be taken whole. A bare English label
    # matching a fragment of one was the single systematic cluster three residual draws found:
    # `Hedge Funds` is not a hedge, `Money Weighted Rate of Return` is not money, and
    # `Cupom Cambial` is not a bond coupon. Registering the compound is what fixes it, so each
    # case asserts both that the fragment stops matching AND that the compound now does.
    ("Com relação à estrutura de custos, os Hedge Funds empregam 2 com 20.", "technical.hedge", False),
    ("Com relação à estrutura de custos, os Hedge Funds empregam 2 com 20.", "technical.hedge_fund", True),
    ("O gestor faz hedge cambial da carteira com contratos futuros.", "technical.hedge", True),
    ("Market Neutral são carteiras cujo objetivo é neutralizar o risco.", "entity.market", False),
    ("Market Neutral são carteiras cujo objetivo é neutralizar o risco.", "technical.market_neutral", True),
    ("O Money Weighted Rate of Return é a TIR da carteira.", "entity.money", False),
    ("O Money Weighted Rate of Return é a TIR da carteira.", "technical.money_weighted_return", True),
    ("O Dow Jones Industrial Average acompanha 30 grandes empresas.", "quantity.mean", False),
    ("O Dow Jones Industrial Average acompanha 30 grandes empresas.", "entity.dow_jones", True),
    ("A gestão por Asset Liability Management se preocupa com o tempo.", "entity.liability", False),
    ("A gestão por Asset Liability Management se preocupa com o tempo.", "technical.asset_liability_management", True),
    ("Um fundo Long and Short possui R$ 10 milhões de patrimônio líquido.", "technical.long_position", False),
    ("Um fundo Long and Short possui R$ 10 milhões de patrimônio líquido.", "technical.long_and_short", True),
    ("O gestor está com posição comprada em dólar.", "technical.long_position", True),
    ("Futuro de Dólar e Cupom Cambial são contratos da B3.", "entity.coupon", False),
    ("Futuro de Dólar e Cupom Cambial são contratos da B3.", "technical.cupom_cambial", True),
    ("O cupom é pago semestralmente pelo título.", "entity.coupon", True),
    # `ativo` is the noun 180 times and the adjective only inside these collocations. Both the
    # noun and the two phrases must hold, or the fix has traded one error class for another.
    ("O valor presente deste ativo acaba sendo valorizado.", "entity.asset", True),
    ("A produtividade de um gestor ativo depende de suas habilidades.", "entity.asset", False),
    ("Com baixo tracking error e retorno ativo, a Enhanced Indexing vai bem.", "entity.asset", False),
    ("Com baixo tracking error e retorno ativo, a Enhanced Indexing vai bem.", "technical.active_return", True),
    ("O diretor deve ser devidamente habilitado pela CVM.", "state.enabled", False),
    # `principal` is the ordinary Portuguese adjective for `main` in 18 of its 28 occurrences.
    ("As TREASURY NOTES tem como principal característica seus vencimentos.", "technical.principal", False),
    ("Pagamentos de juros semestrais = valor principal ajustado pela inflação.", "technical.principal", True),
    # A concept whose definition covered two senses could never register a false positive, which
    # is why splitting it was a correctness fix and not a cosmetic one.
    ("O VaR utiliza a distribuição normal como parâmetro descritivo.", "technical.probability_distribution", True),
    ("O VaR utiliza a distribuição normal como parâmetro descritivo.", "entity.distribution", False),
    ("A distribuição de cotas deve ser realizada por instituições habilitadas.", "entity.distribution", True),
]


class SensePrecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lexicon = load_lexicon()

    def test_bare_ambiguous_tokens_do_not_match_automatically(self):
        for sentence, concept, expected in CASES:
            with self.subTest(concept=concept, sentence=sentence[:48]):
                records = normalize_text(sentence, source="t", kind="text", lexicon=self.lexicon)
                found = concept in (records[0]["concept_ids"] if records else [])
                self.assertEqual(
                    expected,
                    found,
                    f"{concept} {'should' if expected else 'must not'} match here. "
                    f"A bare token that is also an ordinary word needs its collocation as the "
                    f"automatic anchor, with the bare form kept at policy=review.",
                )

    def test_the_income_tax_acronym_stays_ambiguous(self):
        """`IR` is Imposto de Renda and Information Ratio. Neither may win automatically."""
        for sentence in (
            "O IR será de responsabilidade do investidor, com recolhimento via DARF.",
            "Um alto IR indica que o gestor conseguiu resultado ajustado ao risco.",
        ):
            with self.subTest(sentence=sentence[:44]):
                record = normalize_text(sentence, source="t", kind="text", lexicon=self.lexicon)[0]
                self.assertNotIn("entity.income_tax", record["concept_ids"])
                self.assertNotIn("technical.information_ratio", record["concept_ids"])
                candidates = {
                    candidate["concept_id"]
                    for group in record["ambiguous_candidates"]
                    for candidate in group["candidates"]
                }
                self.assertIn("entity.income_tax", candidates)
                self.assertIn("technical.information_ratio", candidates)


if __name__ == "__main__":
    unittest.main()
