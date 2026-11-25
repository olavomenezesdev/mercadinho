"""Testes das regras de estoque com banco em memória (não usam o Supabase real)."""

import logging
import unittest
from datetime import date, datetime
from unittest.mock import patch

from itsdangerous import URLSafeTimedSerializer

import app as app_module
from app import app

try:
    from tests.fake_supabase import FakeSupabase
except ImportError:  # pragma: no cover - quando rodado de dentro da pasta tests
    from fake_supabase import FakeSupabase

HOJE = date(2026, 11, 23)


def dados_iniciais():
    return {
        'usuario': [
            {'id_usuario': 1, 'nome': 'Seu João', 'login': 'dono', 'tipo': 'DONO', 'ativo': True, 'permissoes': []},
            {'id_usuario': 2, 'nome': 'Caixa', 'login': 'caixa', 'tipo': 'FUNCIONARIO', 'ativo': True, 'permissoes': ['caixa']},
        ],
        'categoria': [{'id_categoria': 1, 'nome': 'Mercearia', 'ativo': True}],
        'fornecedor': [{'id_fornecedor': 1, 'nome': 'Atacadão', 'ativo': True}],
        'produto': [
            {'id_produto': 10, 'id_categoria': 1, 'nome': 'Arroz 5kg', 'unidade_medida': 'un', 'estoque_minimo': '5', 'preco': '20.00', 'ativo': True},
            {'id_produto': 11, 'id_categoria': 1, 'nome': 'Leite 1L', 'unidade_medida': 'un', 'estoque_minimo': '2', 'preco': '5.00', 'ativo': True},
            {'id_produto': 12, 'id_categoria': 1, 'nome': 'Produto inativo', 'unidade_medida': 'un', 'estoque_minimo': '5', 'preco': '1.00', 'ativo': False},
        ],
        'lote': [
            {'id_lote': 100, 'id_produto': 10, 'id_fornecedor': 1, 'data_entrada': '2026-11-01', 'data_validade': '2026-12-01', 'quantidade': '3'},
            {'id_lote': 101, 'id_produto': 10, 'id_fornecedor': 1, 'data_entrada': '2026-11-01', 'data_validade': '2027-03-01', 'quantidade': '10'},
            {'id_lote': 102, 'id_produto': 11, 'id_fornecedor': 1, 'data_entrada': '2026-11-01', 'data_validade': '2026-11-20', 'quantidade': '4'},
            {'id_lote': 103, 'id_produto': 11, 'id_fornecedor': 1, 'data_entrada': '2026-11-01', 'data_validade': '2026-11-28', 'quantidade': '1'},
        ],
        'movimentacao': [
            {'id_movimentacao': 1000, 'id_produto': 10, 'id_lote': 100, 'id_usuario': 1, 'tipo': 'ENTRADA', 'quantidade': '3', 'observacao': ''},
            {'id_movimentacao': 1001, 'id_produto': 10, 'id_lote': 101, 'id_usuario': 1, 'tipo': 'ENTRADA', 'quantidade': '10', 'observacao': ''},
            {'id_movimentacao': 1002, 'id_produto': 11, 'id_lote': 102, 'id_usuario': 1, 'tipo': 'ENTRADA', 'quantidade': '4', 'observacao': ''},
            {'id_movimentacao': 1003, 'id_produto': 11, 'id_lote': 103, 'id_usuario': 1, 'tipo': 'ENTRADA', 'quantidade': '1', 'observacao': ''},
        ],
    }


class EstoqueTests(unittest.TestCase):
    def setUp(self):
        app_module.limpar_cache_usuarios()
        # As falhas simuladas geram logs de erro esperados; silenciamos para a saída ficar limpa.
        logging.getLogger('mercadinho').disabled = True
        self.addCleanup(setattr, logging.getLogger('mercadinho'), 'disabled', False)
        self.db = FakeSupabase(**dados_iniciais())
        self.supabase_patcher = patch('app.supabase', self.db)
        self.supabase_patcher.start()
        self.hoje_patcher = patch('app.hoje', return_value=HOJE)
        self.hoje_patcher.start()

        self.original_secret_key = app.config.get('SECRET_KEY')
        app.config['SECRET_KEY'] = 'test-only-secret-key'
        serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='mercadinho-auth')
        self.owner_headers = {'Authorization': f"Bearer {serializer.dumps({'id_usuario': 1})}"}
        self.cashier_headers = {'Authorization': f"Bearer {serializer.dumps({'id_usuario': 2})}"}
        self.client = app.test_client()

    def tearDown(self):
        self.hoje_patcher.stop()
        self.supabase_patcher.stop()
        app.config['SECRET_KEY'] = self.original_secret_key

    def lote(self, lot_id):
        return next((row for row in self.db.rows('lote') if row['id_lote'] == lot_id), None)

    def saidas(self):
        return [row for row in self.db.rows('movimentacao') if row['tipo'] == 'SAIDA']

    def registrar_saida(self, product_id, quantity, headers=None, **extra):
        return self.client.post('/api/movimentacoes', json={
            'tipo': 'SAIDA', 'id_produto': product_id, 'quantidade': quantity, **extra
        }, headers=headers or self.owner_headers)

    def test_saida_usa_o_lote_que_vence_primeiro_e_grava_uma_movimentacao_por_lote(self):
        response = self.registrar_saida(10, 5, motivo='Perda', observacao='Caiu da prateleira')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['valor_total'], '100.00')
        self.assertEqual(len(payload['ids_movimentacao']), 2)
        self.assertEqual(self.lote(100)['quantidade'], '0')
        self.assertEqual(self.lote(101)['quantidade'], '8')
        self.assertEqual(
            [(row['id_lote'], row['quantidade']) for row in self.saidas()],
            [(100, '3'), (101, '2')]
        )
        self.assertTrue(all(row['observacao'] == '[Perda] Caiu da prateleira' for row in self.saidas()))

    def test_saida_sem_motivo_fica_como_venda_e_a_lista_mostra_o_motivo(self):
        self.registrar_saida(10, 1)

        movements = self.client.get('/api/movimentacoes', headers=self.owner_headers).get_json()['items']
        saida = next(item for item in movements if item['tipo'] == 'SAIDA')
        self.assertEqual(saida['motivo'], 'Venda')
        self.assertEqual(saida['observacao'], '')

    def test_saida_com_motivo_avaria_e_gravada_e_listada_como_avaria(self):
        # Mesmo corpo que a tela de Saídas envia (quantidade vem do input como texto).
        response = self.client.post('/api/movimentacoes', json={
            'tipo': 'SAIDA', 'id_produto': 10, 'quantidade': '2', 'motivo': 'Avaria',
            'observacao': 'Embalagem rasgada', 'usuario': 'dono'
        }, headers=self.owner_headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['motivo'], 'Avaria')
        self.assertEqual(self.saidas()[0]['observacao'], '[Avaria] Embalagem rasgada')

        movements = self.client.get('/api/movimentacoes', headers=self.owner_headers).get_json()['items']
        saida = next(item for item in movements if item['tipo'] == 'SAIDA')
        self.assertEqual(saida['motivo'], 'Avaria')
        self.assertEqual(saida['observacao'], 'Embalagem rasgada')

    def test_motivo_invalido_e_recusado(self):
        response = self.registrar_saida(10, 1, motivo='Roubo')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.saidas(), [])

    def test_saida_acima_do_estoque_nao_altera_nada(self):
        response = self.registrar_saida(10, 14)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.lote(100)['quantidade'], '3')
        self.assertEqual(self.lote(101)['quantidade'], '10')
        self.assertEqual(self.saidas(), [])

    def test_falha_no_meio_da_saida_desfaz_tudo(self):
        self.db.fail_on('movimentacao', 'insert', after=1)

        response = self.registrar_saida(10, 5)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.lote(100)['quantidade'], '3')
        self.assertEqual(self.lote(101)['quantidade'], '10')
        self.assertEqual(self.saidas(), [])

    def test_excluir_saida_devolve_a_quantidade_ao_lote(self):
        movement_id = self.registrar_saida(10, 2).get_json()['id_movimentacao']
        self.assertEqual(self.lote(100)['quantidade'], '1')

        response = self.client.delete(f'/api/movimentacoes/{movement_id}', headers=self.owner_headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.lote(100)['quantidade'], '3')
        self.assertEqual(self.saidas(), [])

    def test_excluir_entrada_com_saidas_no_lote_e_bloqueado(self):
        self.registrar_saida(10, 1)

        response = self.client.delete('/api/movimentacoes/1000', headers=self.owner_headers)

        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(self.lote(100))

    def test_excluir_entrada_sem_saidas_remove_o_lote(self):
        response = self.client.delete('/api/movimentacoes/1001', headers=self.owner_headers)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.lote(101))
        self.assertFalse(any(row['id_movimentacao'] == 1001 for row in self.db.rows('movimentacao')))

    def test_falha_ao_gravar_entrada_nao_deixa_lote_sozinho(self):
        self.db.fail_on('movimentacao', 'insert')
        lots_before = len(self.db.rows('lote'))

        response = self.client.post('/api/movimentacoes', json={
            'tipo': 'ENTRADA', 'id_produto': 10, 'quantidade': 6, 'id_fornecedor': 1
        }, headers=self.owner_headers)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(self.db.rows('lote')), lots_before)

    def test_entrada_usa_data_e_hora_de_brasilia(self):
        noite = datetime(2026, 11, 23, 23, 30, tzinfo=app_module.FUSO_HORARIO)
        self.hoje_patcher.stop()
        with patch('app.agora', return_value=noite):
            response = self.client.post('/api/movimentacoes', json={
                'tipo': 'ENTRADA', 'id_produto': 10, 'quantidade': 6, 'id_fornecedor': 1
            }, headers=self.owner_headers)
        self.hoje_patcher.start()

        self.assertEqual(response.status_code, 200)
        lot = self.lote(response.get_json()['id_lote'])
        movement = next(row for row in self.db.rows('movimentacao') if row['id_lote'] == lot['id_lote'])
        self.assertEqual(lot['data_entrada'], '2026-11-23')
        self.assertEqual(movement['data_movimentacao'], '2026-11-23T23:30:00-03:00')

    def test_entrada_com_data_invalida_e_recusada(self):
        response = self.client.post('/api/movimentacoes', json={
            'tipo': 'ENTRADA', 'id_produto': 10, 'quantidade': 6, 'id_fornecedor': 1, 'data_validade': '31/12/2026'
        }, headers=self.owner_headers)

        self.assertEqual(response.status_code, 400)

    def test_avisos_trazem_lote_vencido_e_validade_proxima(self):
        alerts = self.client.get('/api/alertas', headers=self.owner_headers).get_json()['items']

        self.assertEqual(
            sorted((alert['tipo'], alert.get('id_lote')) for alert in alerts),
            [('LOTE_VENCIDO', 102), ('VALIDADE_PROXIMA', 100), ('VALIDADE_PROXIMA', 103)]
        )

    def test_relatorio_conta_lotes_vencidos_e_ignora_produto_inativo(self):
        summary = self.client.get('/api/relatorios', headers=self.owner_headers).get_json()['summary']

        self.assertEqual(summary['lotes_vencidos'], 1)
        self.assertEqual(summary['validade_proxima'], 2)
        self.assertEqual(summary['estoque_baixo'], 0)

    def test_produtos_trazem_a_proxima_validade(self):
        items = self.client.get('/api/produtos', headers=self.owner_headers).get_json()['items']
        by_id = {item['id_produto']: item for item in items}

        self.assertEqual(by_id[10]['data_validade'], '2026-12-01')
        self.assertTrue(by_id[10]['alerta_validade'])
        self.assertEqual(by_id[11]['data_validade'], '2026-11-20')
        self.assertIsNone(by_id[12]['data_validade'])
        self.assertFalse(by_id[12]['alerta_validade'])

    def test_erro_no_banco_responde_503_com_mensagem(self):
        self.db.fail_on('produto', 'select')

        response = self.client.get('/api/produtos', headers=self.owner_headers)

        self.assertEqual(response.status_code, 503)
        self.assertIn('banco de dados', response.get_json()['message'])

    def test_banco_nao_configurado_responde_503(self):
        with patch('app.supabase', None):
            response = self.client.get('/api/categorias', headers=self.owner_headers)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['message'], 'Banco não configurado.')

    def test_cors_libera_somente_origens_conhecidas(self):
        local = self.client.get('/api/health', headers={'Origin': 'http://127.0.0.1:8000'})
        externo = self.client.get('/api/health', headers={'Origin': 'https://site-qualquer.com'})

        self.assertEqual(local.headers.get('Access-Control-Allow-Origin'), 'http://127.0.0.1:8000')
        self.assertNotIn('Access-Control-Allow-Origin', externo.headers)

    def test_funcionario_do_caixa_registra_venda(self):
        response = self.registrar_saida(11, 2, headers=self.cashier_headers, motivo='venda', observacao='Venda via caixa - PIX')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['motivo'], 'Venda')
        self.assertEqual(self.saidas()[0]['observacao'], '[Venda] Venda via caixa - PIX')

    def registrar_venda(self, itens, forma_pagamento='PIX', headers=None):
        return self.client.post('/api/vendas', json={
            'itens': itens, 'forma_pagamento': forma_pagamento
        }, headers=headers or self.cashier_headers)

    def test_venda_do_caixa_grava_todos_os_itens_de_uma_vez(self):
        response = self.registrar_venda([
            {'id_produto': 10, 'quantidade': 5},
            {'id_produto': 11, 'quantidade': 1},
        ])

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        # Arroz: 3 do lote 100 + 2 do lote 101; Leite: 1 do lote 102 (vence primeiro).
        self.assertEqual(payload['valor_total'], '105.00')
        self.assertEqual(len(payload['ids_movimentacao']), 3)
        self.assertEqual(self.lote(100)['quantidade'], '0')
        self.assertEqual(self.lote(101)['quantidade'], '8')
        self.assertEqual(self.lote(102)['quantidade'], '3')
        self.assertEqual(
            [(row['id_lote'], row['quantidade']) for row in self.saidas()],
            [(100, '3'), (101, '2'), (102, '1')]
        )
        self.assertTrue(all(row['observacao'] == '[Venda] Venda via caixa - PIX' for row in self.saidas()))
        self.assertTrue(all(row['id_usuario'] == 2 for row in self.saidas()))

    def test_venda_com_item_sem_estoque_nao_grava_nada(self):
        response = self.registrar_venda([
            {'id_produto': 10, 'quantidade': 2},
            {'id_produto': 11, 'quantidade': 6},
        ])

        self.assertEqual(response.status_code, 400)
        self.assertIn('Leite 1L', response.get_json()['message'])
        self.assertEqual(self.lote(100)['quantidade'], '3')
        self.assertEqual(self.lote(102)['quantidade'], '4')
        self.assertEqual(self.saidas(), [])

    def test_venda_soma_itens_repetidos_ao_conferir_o_estoque(self):
        response = self.registrar_venda([
            {'id_produto': 11, 'quantidade': 3},
            {'id_produto': 11, 'quantidade': 3},
        ])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.saidas(), [])

    def test_falha_no_meio_da_venda_desfaz_tudo(self):
        # Grava as 2 movimentações do arroz e falha na do leite.
        self.db.fail_on('movimentacao', 'insert', after=2)

        response = self.registrar_venda([
            {'id_produto': 10, 'quantidade': 5},
            {'id_produto': 11, 'quantidade': 1},
        ])

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.lote(100)['quantidade'], '3')
        self.assertEqual(self.lote(101)['quantidade'], '10')
        self.assertEqual(self.lote(102)['quantidade'], '4')
        self.assertEqual(self.saidas(), [])

    def test_venda_com_forma_de_pagamento_invalida_e_recusada(self):
        response = self.registrar_venda([{'id_produto': 10, 'quantidade': 1}], forma_pagamento='FIADO')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.saidas(), [])

    def movimentacao_listada(self, movement_id):
        items = self.client.get('/api/movimentacoes', headers=self.owner_headers).get_json()['items']
        return next(item for item in items if item['id_movimentacao'] == movement_id)

    def test_mudar_o_preco_nao_muda_o_valor_de_venda_antiga(self):
        movement_id = self.registrar_venda([{'id_produto': 11, 'quantidade': 2}]).get_json()['ids_movimentacao'][0]

        response = self.client.put('/api/produtos/11', json={'preco': '9.90'}, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200)

        venda = self.movimentacao_listada(movement_id)
        self.assertEqual(venda['valor_unitario'], '5.00')
        self.assertEqual(venda['valor_total'], '10.00')

    def test_movimentacao_sem_valor_gravado_usa_o_preco_atual(self):
        # As entradas dos dados iniciais não têm valor gravado.
        entrada = self.movimentacao_listada(1000)

        self.assertEqual(entrada['valor_unitario'], '20.00')
        self.assertEqual(entrada['valor_total'], '60.00')

    def test_produto_inativo_nao_aceita_entrada_nem_saida(self):
        # O produto 12 está inativo; damos saldo a ele para a saída não falhar por estoque.
        self.db.tables['lote'].append({
            'id_lote': 104, 'id_produto': 12, 'id_fornecedor': 1, 'data_entrada': '2026-11-01',
            'data_validade': None, 'quantidade': '5'
        })
        lots_before = len(self.db.rows('lote'))
        movements_before = len(self.db.rows('movimentacao'))

        entrada = self.client.post('/api/movimentacoes', json={
            'tipo': 'ENTRADA', 'id_produto': 12, 'quantidade': 3, 'id_fornecedor': 1
        }, headers=self.owner_headers)
        saida = self.registrar_saida(12, 1)
        venda = self.registrar_venda([{'id_produto': 12, 'quantidade': 1}])

        for response in (entrada, saida, venda):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()['message'], 'Produto inativo.')
        self.assertEqual(len(self.db.rows('lote')), lots_before)
        self.assertEqual(len(self.db.rows('movimentacao')), movements_before)
        self.assertEqual(self.lote(104)['quantidade'], '5')

    def test_movimentacoes_trazem_o_fornecedor_do_lote(self):
        entrada = self.movimentacao_listada(1000)

        self.assertEqual(entrada['id_fornecedor'], 1)
        self.assertEqual(entrada['fornecedor'], 'Atacadão')

    def criar_fornecedor(self, **dados):
        return self.client.post('/api/fornecedores', json=dados, headers=self.owner_headers)

    def test_nome_de_fornecedor_repetido_com_maiusculas_e_espacos_diferentes(self):
        # O fornecedor 1 (Atacadão) também conta se estiver inativo.
        self.db.tables['fornecedor'][0]['ativo'] = False
        fornecedores_antes = len(self.db.rows('fornecedor'))

        response = self.criar_fornecedor(nome='  ATACADÃO  ')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['message'], 'Já existe um fornecedor com este nome.')
        self.assertEqual(len(self.db.rows('fornecedor')), fornecedores_antes)

        response = self.criar_fornecedor(nome='Distribuidora   Boa  Vista')
        self.assertEqual(response.status_code, 200)
        response = self.criar_fornecedor(nome='distribuidora boa vista')
        self.assertEqual(response.status_code, 409)

    def test_telefone_de_fornecedor_repetido_com_formatacao_diferente(self):
        self.db.tables['fornecedor'][0]['telefone'] = '(11) 98839-1234'

        criar = self.criar_fornecedor(nome='Outro', telefone='11988391234')
        self.assertEqual(criar.status_code, 409)
        self.assertEqual(criar.get_json()['message'], 'Já existe um fornecedor com este telefone.')

        outro_id = self.criar_fornecedor(nome='Outro', telefone='(11) 3333-4444').get_json()['id_fornecedor']
        editar = self.client.put(f'/api/fornecedores/{outro_id}', json={
            'telefone': '11 98839 1234'
        }, headers=self.owner_headers)
        self.assertEqual(editar.status_code, 409)
        self.assertEqual(editar.get_json()['message'], 'Já existe um fornecedor com este telefone.')

    def test_email_de_fornecedor_repetido_diz_qual_campo_repetiu(self):
        self.db.tables['fornecedor'][0]['email'] = 'vendas@atacadao.com'

        response = self.criar_fornecedor(nome='Outro', email='vendas@atacadao.com')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['message'], 'Já existe um fornecedor com este e-mail.')

    def test_editar_o_proprio_fornecedor_sem_mudar_nada_passa(self):
        self.db.tables['fornecedor'][0].update({'telefone': '(11) 98839-1234', 'email': 'vendas@atacadao.com'})

        response = self.client.put('/api/fornecedores/1', json={
            'nome': 'Atacadão', 'telefone': '(11) 98839-1234', 'email': 'vendas@atacadao.com', 'ativo': True
        }, headers=self.owner_headers)

        self.assertEqual(response.status_code, 200)

    def test_telefone_de_fornecedor_invalido_e_recusado(self):
        fornecedores_antes = len(self.db.rows('fornecedor'))

        for telefone in ('*11', '1234', '(11) 98839-12345', '11 9883A-1234'):
            response = self.criar_fornecedor(nome=f'Fornecedor {telefone}', telefone=telefone)
            self.assertEqual(response.status_code, 400, telefone)
            self.assertIn('Telefone inválido', response.get_json()['message'])
        self.assertEqual(len(self.db.rows('fornecedor')), fornecedores_antes)

        # Vazio é aceito; 10 e 11 dígitos também.
        self.assertEqual(self.criar_fornecedor(nome='Sem telefone', telefone='').status_code, 200)
        self.assertEqual(self.criar_fornecedor(nome='Fixo', telefone='(11) 3333-4444').status_code, 200)
        self.assertEqual(self.criar_fornecedor(nome='Celular', telefone='11977776666').status_code, 200)

    def test_editar_fornecedor_com_telefone_antigo_fora_do_padrao_ainda_permite_inativar(self):
        self.db.tables['fornecedor'][0]['telefone'] = '*11'

        response = self.client.put('/api/fornecedores/1', json={
            'nome': 'Atacadão', 'telefone': '*11', 'ativo': False
        }, headers=self.owner_headers)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.db.rows('fornecedor')[0]['ativo'])

    def test_email_de_fornecedor_repetido_ignora_maiusculas(self):
        self.db.tables['fornecedor'][0]['email'] = 'vendas@atacadao.com'

        criar = self.client.post('/api/fornecedores', json={
            'nome': 'Outro', 'email': 'Vendas@Atacadao.COM'
        }, headers=self.owner_headers)
        self.assertEqual(criar.status_code, 409)

        outro_id = self.client.post('/api/fornecedores', json={
            'nome': 'Outro', 'email': 'contato@outro.com'
        }, headers=self.owner_headers).get_json()['id_fornecedor']
        editar = self.client.put(f'/api/fornecedores/{outro_id}', json={
            'email': ' VENDAS@atacadao.com '
        }, headers=self.owner_headers)
        self.assertEqual(editar.status_code, 409)

        # O próprio fornecedor pode trocar só as maiúsculas do e-mail dele.
        proprio = self.client.put('/api/fornecedores/1', json={
            'email': 'VENDAS@ATACADAO.COM'
        }, headers=self.owner_headers)
        self.assertEqual(proprio.status_code, 200)

    def test_venda_exige_permissao_de_caixa_ou_saidas(self):
        self.db.tables['usuario'].append({
            'id_usuario': 3, 'nome': 'Estoquista', 'login': 'estoque', 'tipo': 'FUNCIONARIO',
            'ativo': True, 'permissoes': ['entradas']
        })
        serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='mercadinho-auth')
        headers = {'Authorization': f"Bearer {serializer.dumps({'id_usuario': 3})}"}

        response = self.registrar_venda([{'id_produto': 10, 'quantidade': 1}], headers=headers)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.saidas(), [])


    # --- Desempenho: cada tabela é lida uma vez por chamada e o usuário do token fica em cache ---

    def contar_consultas(self):
        chamadas = []
        original = self.db.table

        def table(nome):
            chamadas.append(nome)
            return original(nome)

        self.db.table = table
        self.addCleanup(setattr, self.db, 'table', original)
        return chamadas

    def test_lista_de_movimentacoes_le_cada_tabela_uma_vez(self):
        chamadas = self.contar_consultas()
        response = self.client.get('/api/movimentacoes', headers=self.owner_headers)
        self.assertEqual(response.status_code, 200)
        for tabela in ('movimentacao', 'produto', 'categoria', 'lote', 'fornecedor', 'usuario'):
            self.assertEqual(chamadas.count(tabela), 1, tabela)

    def test_lista_de_produtos_nao_repete_consultas(self):
        chamadas = self.contar_consultas()
        response = self.client.get('/api/produtos', headers=self.owner_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(chamadas.count('produto'), 1)
        self.assertEqual(chamadas.count('lote'), 1)
        self.assertEqual(chamadas.count('categoria'), 1)

    def test_usuario_do_token_fica_em_cache_entre_chamadas(self):
        chamadas = self.contar_consultas()
        self.client.get('/api/categorias', headers=self.owner_headers)
        self.client.get('/api/categorias', headers=self.owner_headers)
        self.assertEqual(chamadas.count('usuario'), 1)

    def test_usuario_inativado_pelo_dono_perde_o_acesso_na_hora(self):
        self.assertEqual(self.client.get('/api/produtos', headers=self.cashier_headers).status_code, 200)
        resposta = self.client.put('/api/usuarios/2', json={'ativo': False}, headers=self.owner_headers)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.client.get('/api/produtos', headers=self.cashier_headers).status_code, 401)


if __name__ == '__main__':
    unittest.main()
