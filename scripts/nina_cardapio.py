#!/usr/bin/env python3
"""
nina_cardapio.py
Roda todo domingo de madrugada via GitHub Actions.
Coleta contexto de saúde da Verônica e pede à Nina (Claude)
que monte e publique o cardápio da semana.
"""

import os
import json
import requests
from datetime import datetime, date, timedelta

# ── Configurações ─────────────────────────────────────────────
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_SERVICE_KEY"]
NINA_API_KEY    = os.environ["NINA_API_KEY"]
CARDAPIO_API    = f"{SUPABASE_URL}/functions/v1/cardapio-api"

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
NINA_HEADERS = {
    "Content-Type": "application/json",
    "x-api-key": NINA_API_KEY,
}

# ── 1. Coleta contexto de saúde ───────────────────────────────
def get_saude():
    ctx = {}

    # Último peso
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jornada_pesos",
        headers=SB_HEADERS,
        params={"select": "data,peso,gordura_pct,massa_musc_total_kg",
                "order": "data.desc", "limit": "3"},
    )
    ctx["pesos_recentes"] = r.json() if r.ok else []

    # Última bioimpedância
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jornada_bioimpedancia",
        headers=SB_HEADERS,
        params={"select": "data,proteina_pct,gordura_pct,agua_pct,metabolismo",
                "order": "data.desc", "limit": "1"},
    )
    ctx["bioimpedancia"] = r.json()[0] if r.ok and r.json() else None

    # Suplementos em uso
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jornada_suplementos",
        headers=SB_HEADERS,
        params={"select": "nome,dose,horario", "order": "nome"},
    )
    ctx["suplementos"] = r.json() if r.ok else []

    # Medicações
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/jornada_medicacao",
        headers=SB_HEADERS,
        params={"select": "nome,dose,horario,observacao"},
    )
    ctx["medicacao"] = r.json() if r.ok else []

    return ctx

# ── 2. Coleta rotina semanal (restrições por dia) ─────────────
def get_rotina():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/amanda_rotina",
        headers=SB_HEADERS,
        params={"select": "*", "order": "dia_semana,hora_inicio"},
    )
    return r.json() if r.ok else []

# ── 3. Coleta receitas disponíveis ────────────────────────────
def get_receitas():
    r = requests.post(
        CARDAPIO_API,
        headers=NINA_HEADERS,
        json={"acao": "listar_receitas"},
    )
    if r.ok:
        return r.json().get("receitas_por_categoria", {})
    return {}

# ── 4. Chama Claude (Nina) ────────────────────────────────────
def pedir_cardapio_para_nina(saude, rotina, receitas):
    hoje = date.today()
    # Próximo domingo (início da semana do cardápio)
    dias_ate_domingo = (6 - hoje.weekday()) % 7
    if dias_ate_domingo == 0:
        dias_ate_domingo = 7
    proximo_domingo = hoje + timedelta(days=dias_ate_domingo)

    dias_semana = []
    for i in range(7):
        d = proximo_domingo + timedelta(days=i)
        dias_semana.append(d.strftime("%A %d/%m"))  # ex: Sunday 15/06

    rotina_txt = json.dumps(rotina, ensure_ascii=False, indent=2)
    saude_txt  = json.dumps(saude,  ensure_ascii=False, indent=2)

    # Monta lista legível de receitas
    receitas_txt = ""
    for cat, nomes in receitas.items():
        receitas_txt += f"\n{cat}:\n"
        for n in nomes:
            receitas_txt += f"  - {n}\n"

    system = """Você é Nina, nutricionista virtual da Verônica.
Seu trabalho agora é montar o cardápio da semana usando a API de cardápio.

REGRAS OBRIGATÓRIAS:
1. Cada refeição deve ter entre 2 e 4 pratos separados (ex: arroz, feijão, frango)
2. Use APENAS receitas que existem no catálogo fornecido
3. Se precisar de uma receita que não existe, use a ação cadastrar_receita ANTES
4. Nunca agrupe pratos num único nome (ex: NUNCA "Frango + arroz + salada")
5. Varie as proteínas ao longo da semana
6. Considere a rotina semanal para ajustar o tempo de preparo por dia
7. Considere os dados de saúde para balancear macros

RESPONDA APENAS com um JSON válido neste formato exato (sem texto adicional):
{
  "cardapio": [
    {
      "dia": "domingo",
      "refeicoes": [
        {"refeicao": "cafe", "pratos": ["Receita 1", "Receita 2"]},
        {"refeicao": "almoco", "pratos": ["Receita A", "Receita B", "Receita C"]},
        {"refeicao": "lanche", "pratos": ["Receita X"]},
        {"refeicao": "jantar", "pratos": ["Receita M", "Receita N"]}
      ]
    }
  ]
}
"""

    user = f"""Monte o cardápio da semana de {dias_semana[0]} a {dias_semana[6]}.

=== DADOS DE SAÚDE DA VERÔNICA ===
{saude_txt}

=== ROTINA SEMANAL (use para ajustar tempo de preparo) ===
{rotina_txt}

=== RECEITAS DISPONÍVEIS NO CATÁLOGO ===
{receitas_txt}

Instruções adicionais:
- Café da manhã: leve, rápido, proteico (ex: ovo, iogurte, vitamina)
- Almoço: completo (proteína + carboidrato + salada/legume)
- Lanche: leve (1-2 itens)
- Jantar: mais leve que o almoço, preferencialmente menos carboidrato
- Se em algum dia a rotina indicar almoço rápido (menos de 20min preparo),
  escolha receitas com tempo ≤ 20 min para aquele dia
- Varie as proteínas: não repita a mesma no almoço e jantar do mesmo dia
"""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=120,
    )

    if not response.ok:
        raise Exception(f"Erro na API do Claude: {response.status_code} {response.text}")

    content = response.json()["content"][0]["text"]

    # Extrai JSON da resposta
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Tenta extrair bloco JSON se vier com texto ao redor
        import re
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise Exception(f"Nina não retornou JSON válido: {content[:500]}")

# ── 5. Publica o cardápio via API ─────────────────────────────
def publicar_cardapio(cardapio_json):
    # Limpa a semana
    r = requests.post(CARDAPIO_API, headers=NINA_HEADERS,
                      json={"acao": "limpar_semana"})
    if not r.ok:
        raise Exception(f"Erro ao limpar semana: {r.text}")
    print("✓ Semana limpa")

    erros = []
    sucessos = 0

    for dia_block in cardapio_json.get("cardapio", []):
        dia = dia_block["dia"]
        for ref_block in dia_block.get("refeicoes", []):
            refeicao = ref_block["refeicao"]
            pratos   = ref_block["pratos"]

            payload = {
                "acao": "adicionar",
                "dia": dia,
                "refeicao": refeicao,
                "pratos": pratos,
            }
            r = requests.post(CARDAPIO_API, headers=NINA_HEADERS, json=payload)

            if r.ok:
                print(f"  ✓ {dia} / {refeicao}: {pratos}")
                sucessos += 1
            else:
                err = r.json()
                print(f"  ✗ {dia} / {refeicao}: {err.get('erro','?')}")
                if "pratos_nao_encontrados" in err:
                    print(f"    Pratos inválidos: {err['pratos_nao_encontrados']}")
                erros.append({"dia": dia, "refeicao": refeicao, "erro": err})

    return sucessos, erros

# ── MAIN ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🍳 Nina Cardápio — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*50)

    print("\n📊 Coletando dados de saúde...")
    saude = get_saude()
    print(f"  Pesos: {len(saude['pesos_recentes'])} registros")
    print(f"  Bioimpedância: {'sim' if saude['bioimpedancia'] else 'não'}")

    print("\n📅 Coletando rotina semanal...")
    rotina = get_rotina()
    print(f"  {len(rotina)} entradas de rotina")

    print("\n🥗 Buscando receitas disponíveis...")
    receitas = get_receitas()
    total = sum(len(v) for v in receitas.values())
    print(f"  {total} receitas em {len(receitas)} categorias")

    print("\n🧠 Pedindo cardápio para a Nina...")
    cardapio_json = pedir_cardapio_para_nina(saude, rotina, receitas)
    dias = len(cardapio_json.get("cardapio", []))
    print(f"  Nina montou {dias} dias de cardápio")

    print("\n📤 Publicando cardápio...")
    sucessos, erros = publicar_cardapio(cardapio_json)

    print("\n" + "="*50)
    print(f"✅ Concluído: {sucessos} refeições publicadas, {len(erros)} erros")

    if erros:
        print("\n⚠️  Erros encontrados:")
        for e in erros:
            print(f"  - {e['dia']} / {e['refeicao']}: {e['erro']}")
        # Não falha o workflow — cardápio parcial é melhor que nada
        exit(0)
