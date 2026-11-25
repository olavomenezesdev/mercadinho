import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, g, jsonify, redirect, request, send_from_directory
from dotenv import load_dotenv
from supabase import create_client
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
TOKEN_MAX_AGE_SECONDS = 8 * 60 * 60
TOKEN_SALT = 'mercadinho-auth'
TELAS_PERMITIDAS = (
    'painel',
    'caixa',
    'produtos',
    'entradas',
    'saidas',
    'relatorios',
    'fornecedores'
)
PERMISSOES_PADRAO_FUNCIONARIO = ('painel', 'caixa', 'entradas', 'saidas', 'produtos')

# Horário de Brasília (UTC-3). O Brasil não tem horário de verão desde 2019,
# então um fuso fixo evita depender do pacote tzdata no Windows.
FUSO_HORARIO = timezone(timedelta(hours=-3), 'America/Sao_Paulo')
MOTIVOS_SAIDA = ('Venda', 'Perda', 'Vencimento', 'Avaria')
FORMAS_PAGAMENTO = ('DINHEIRO', 'CARTAO', 'PIX')
MOTIVO_NA_OBSERVACAO = re.compile(r'^\[(Venda|Perda|Vencimento|Avaria)\]\s*(.*)$', re.DOTALL)

logger = logging.getLogger('mercadinho')


def _ler_dias_validade_proxima():
    try:
        dias = int(os.getenv('DIAS_VALIDADE_PROXIMA', '30'))
    except ValueError:
        return 30
    return dias if dias > 0 else 30


DIAS_VALIDADE_PROXIMA = _ler_dias_validade_proxima()
ORIGENS_PERMITIDAS = {
    origem.strip().rstrip('/')
    for origem in os.getenv('ALLOWED_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000').split(',')
    if origem.strip()
}


def agora():
    return datetime.now(FUSO_HORARIO)


def hoje():
    return agora().date()


class BancoIndisponivel(Exception):
    """Falha ao acessar o banco. Vira uma resposta 503 com mensagem clara."""


@app.errorhandler(BancoIndisponivel)
def handle_banco_indisponivel(error):
    return jsonify({'ok': False, 'message': str(error)}), 503


@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/login')
def serve_login_alias():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/cadastro')
def serve_cadastro_alias():
    return redirect('/login')


@app.route('/painel')
def serve_painel_alias():
    return send_from_directory(app.static_folder, 'painel.html')


@app.route('/produtos')
def serve_produtos_alias():
    return send_from_directory(app.static_folder, 'produtos.html')


@app.route('/entradas')
def serve_entradas_alias():
    return send_from_directory(app.static_folder, 'entradas.html')


@app.route('/saidas')
def serve_saidas_alias():
    return send_from_directory(app.static_folder, 'saidas.html')


@app.route('/relatorios')
def serve_relatorios_alias():
    return send_from_directory(app.static_folder, 'relatorios.html')


@app.route('/caixa')
def serve_caixa_alias():
    return send_from_directory(app.static_folder, 'caixa.html')


@app.route('/fornecedores')
def serve_fornecedores_alias():
    return send_from_directory(app.static_folder, 'fornecedores.html')


@app.route('/usuarios')
def serve_usuarios_alias():
    return send_from_directory(app.static_folder, 'usuarios.html')


@app.route('/<path:filename>')
def serve_static(filename):
    if filename == 'api' or filename.startswith('api/'):
        return jsonify({'ok': False, 'message': 'Rota da API não encontrada.'}), 404
    return send_from_directory(app.static_folder, filename)


def _api_http_error(error, status_code, message):
    if request.path == '/api' or request.path.startswith('/api/'):
        return jsonify({'ok': False, 'message': message}), status_code
    return error


@app.errorhandler(404)
def handle_not_found(error):
    return _api_http_error(error, 404, 'Rota da API não encontrada.')


@app.errorhandler(405)
def handle_method_not_allowed(error):
    return _api_http_error(error, 405, 'Método não permitido para esta rota da API.')


@app.errorhandler(500)
def handle_internal_server_error(error):
    return _api_http_error(error, 500, 'Erro interno do servidor.')


@app.after_request
def add_cors_headers(response):
    # As telas são servidas pelo próprio Flask, então só origens conhecidas
    # recebem permissão de CORS (configuráveis em ALLOWED_ORIGINS).
    origem = str(request.headers.get('Origin') or '').rstrip('/')
    if origem and origem in ORIGENS_PERMITIDAS:
        response.headers['Access-Control-Allow-Origin'] = origem
        response.headers['Vary'] = 'Origin'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


@app.before_request
def handle_api_options():
    if request.method == 'OPTIONS' and request.path.startswith('/api/'):
        return '', 204
    return None


supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_KEY')

if supabase_url:
    parsed_supabase_url = urlsplit(supabase_url.strip())
    if parsed_supabase_url.path.rstrip('/') == '/rest/v1':
        supabase_url = urlunsplit((
            parsed_supabase_url.scheme,
            parsed_supabase_url.netloc,
            '',
            '',
            ''
        ))

if supabase_url and supabase_key:
    try:
        supabase = create_client(supabase_url, supabase_key)
    except Exception:  # pragma: no cover
        supabase = None
else:
    supabase = None


def _consulta_no_banco(table_name, select='*', **kwargs):
    if not supabase:
        raise BancoIndisponivel('Banco não configurado.')

    try:
        query = supabase.table(table_name).select(select)
        for key, value in kwargs.items():
            if value is not None:
                query = query.eq(key, value)
        response = query.execute()
        return response.data or []
    except Exception as error:
        logger.exception('Falha ao consultar a tabela %s', table_name)
        raise BancoIndisponivel(
            'Não foi possível acessar o banco de dados. Verifique a conexão e tente novamente.'
        ) from error


def _memoria_da_requisicao():
    """Guarda o resultado das leituras de uma requisição GET para não repetir a mesma consulta."""
    try:
        if request.method != 'GET':
            return None
        if not hasattr(g, '_consultas'):
            g._consultas = {}
        return g._consultas
    except RuntimeError:  # fora de uma requisição (scripts e testes)
        return None


def _chave_consulta(table_name, select, kwargs):
    return (table_name, select, tuple(sorted((k, str(v)) for k, v in kwargs.items() if v is not None)))


def safe_query(table_name, select='*', **kwargs):
    memoria = _memoria_da_requisicao()
    chave = _chave_consulta(table_name, select, kwargs)
    if memoria is not None and chave in memoria:
        return memoria[chave]
    itens = _consulta_no_banco(table_name, select, **kwargs)
    if memoria is not None:
        memoria[chave] = itens
    return itens


_executor_consultas = ThreadPoolExecutor(max_workers=8)


def buscar_tabelas(*tabelas):
    """Lê várias tabelas inteiras ao mesmo tempo (em paralelo) e guarda para o resto da requisição.

    Cada consulta ao Supabase leva uma ida e volta pela internet; feitas uma depois da outra,
    o tempo soma. Em paralelo, a espera é a da consulta mais lenta.
    """
    memoria = _memoria_da_requisicao()
    if memoria is None:
        return
    faltando = [t for t in dict.fromkeys(tabelas) if _chave_consulta(t, '*', {}) not in memoria]
    # Nas threads não há requisição ativa, então safe_query vai direto ao banco.
    futuros = {t: _executor_consultas.submit(safe_query, t, '*') for t in faltando}
    for tabela, futuro in futuros.items():
        memoria[_chave_consulta(tabela, '*', {})] = futuro.result()


def safe_insert(table_name, payload):
    if not supabase:
        return None

    try:
        response = supabase.table(table_name).insert(payload).execute()
        if response.data:
            return response.data[0]
        return None
    except Exception:
        logger.exception('Falha ao gravar na tabela %s', table_name)
        return None


def safe_delete(table_name, column, value):
    if not supabase or value is None:
        return False

    try:
        supabase.table(table_name).delete().eq(column, value).execute()
        return True
    except Exception:
        logger.exception('Falha ao excluir da tabela %s', table_name)
        return False


def save_user_password_hash(user_id, password_hash):
    if not supabase or user_id is None:
        return False

    try:
        response = (
            supabase.table('usuario')
            .update({'senha': password_hash})
            .eq('id_usuario', user_id)
            .execute()
        )
        return bool(response.data)
    except Exception:
        return False


def safe_update(table_name, row_id, payload):
    if not supabase or row_id is None:
        return None

    try:
        response = supabase.table(table_name).update(payload).eq('id_lote', row_id).execute()
        if response.data:
            return response.data[0]
        return None
    except Exception:
        logger.exception('Falha ao atualizar a tabela %s', table_name)
        return None


def atualizar_saldo_lote(lot_id, quantidade):
    return safe_update('lote', lot_id, {'quantidade': str(quantidade)}) is not None


def parse_decimal(raw_value):
    if raw_value in (None, '', ' '):
        return Decimal('0')

    try:
        value = Decimal(str(raw_value).replace(',', '.'))
    except (InvalidOperation, ValueError):
        raise ValueError('Valor numérico inválido.')

    return value


def get_user_by_login(login_name):
    login_name = str(login_name or '').strip().lower()
    if not login_name:
        return None

    for item in safe_query('usuario', '*'):
        if str(item.get('login', '')).strip().lower() == login_name and bool(item.get('ativo', False)):
            return item
    return None


def _token_serializer():
    secret_key = app.config.get('SECRET_KEY')
    if not secret_key:
        return None
    return URLSafeTimedSerializer(secret_key, salt=TOKEN_SALT)


def usuario_do_token(request):
    authorization = str(request.headers.get('Authorization') or '')
    scheme, separator, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not separator or not token.strip():
        return None

    serializer = _token_serializer()
    if serializer is None:
        return None

    try:
        identity = serializer.loads(token.strip(), max_age=TOKEN_MAX_AGE_SECONDS)
    except BadSignature:
        return None

    if not isinstance(identity, dict) or identity.get('id_usuario') is None:
        return None

    user = _usuario_em_cache(identity['id_usuario'])
    if user is None:
        users = safe_query('usuario', '*', id_usuario=identity['id_usuario'])
        if not users:
            return None
        user = users[0]
        _guardar_usuario(identity['id_usuario'], user)
    if not bool(user.get('ativo', False)):
        return None
    return user


# O usuário do token fica guardado por pouco tempo para não ir ao banco em toda chamada.
# Mudanças feitas na tela Usuários limpam o cache na hora; feitas direto no Supabase
# ou por outro servidor, valem em até USUARIO_CACHE_SEGUNDOS.
USUARIO_CACHE_SEGUNDOS = 30
_cache_usuarios = {}
_trava_cache_usuarios = threading.Lock()


def _usuario_em_cache(id_usuario):
    with _trava_cache_usuarios:
        item = _cache_usuarios.get(str(id_usuario))
        if item and item[1] > time.monotonic():
            return item[0]
    return None


def _guardar_usuario(id_usuario, user):
    with _trava_cache_usuarios:
        _cache_usuarios[str(id_usuario)] = (user, time.monotonic() + USUARIO_CACHE_SEGUNDOS)


def limpar_cache_usuarios():
    with _trava_cache_usuarios:
        _cache_usuarios.clear()


def _normalize_permissions(raw_permissions, default_permissions=None):
    if raw_permissions is None:
        if default_permissions is None:
            raise ValueError('A lista de permissões é inválida.')
        raw_permissions = list(default_permissions)
    if not isinstance(raw_permissions, list) or any(
        not isinstance(permission, str) for permission in raw_permissions
    ):
        raise ValueError('A lista de permissões é inválida.')

    unknown_permissions = set(raw_permissions) - set(TELAS_PERMITIDAS)
    if unknown_permissions:
        raise ValueError('A lista contém permissões desconhecidas.')

    selected_permissions = set(raw_permissions)
    return [screen for screen in TELAS_PERMITIDAS if screen in selected_permissions]


def _public_user(user):
    return {
        'id_usuario': user.get('id_usuario'),
        'nome': user.get('nome') or '',
        'login': user.get('login') or '',
        'tipo': user.get('tipo') or 'FUNCIONARIO',
        'permissoes': user.get('permissoes') or [],
        'ativo': bool(user.get('ativo', False))
    }


def _unauthorized_response():
    return jsonify({'ok': False, 'erro': 'Autenticação necessária'}), 401


def _forbidden_response():
    return jsonify({'ok': False, 'erro': 'Sem permissão'}), 403


def _user_permissions(usuario):
    permissions = usuario.get('permissoes') or []
    if isinstance(permissions, str):
        return {permissions}
    return set(permissions)


def _is_owner(usuario):
    return str(usuario.get('tipo') or '').strip().upper() == 'DONO'


def _has_permission(usuario, tela):
    return _is_owner(usuario) or tela in _user_permissions(usuario)


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        usuario = usuario_do_token(request)
        if not usuario:
            return _unauthorized_response()
        g.usuario_autenticado = usuario
        return view_func(*args, **kwargs)
    return wrapped


def permissao(tela):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            usuario = getattr(g, 'usuario_autenticado', None)
            if usuario is None:
                return _unauthorized_response()
            if not _has_permission(usuario, tela):
                return _forbidden_response()
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def permissao_qualquer(*telas):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            usuario = getattr(g, 'usuario_autenticado', None)
            if usuario is None:
                return _unauthorized_response()
            if not _is_owner(usuario) and not _user_permissions(usuario).intersection(telas):
                return _forbidden_response()
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def somente_dono(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        usuario = getattr(g, 'usuario_autenticado', None)
        if usuario is None:
            return _unauthorized_response()
        if not _is_owner(usuario):
            return _forbidden_response()
        return view_func(*args, **kwargs)
    return wrapped


def permissao_movimentacao(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        usuario = getattr(g, 'usuario_autenticado', None)
        if usuario is None:
            return _unauthorized_response()

        payload = request.get_json(silent=True) or {}
        tipo = str(payload.get('tipo', 'ENTRADA')).upper().strip()
        if tipo not in {'ENTRADA', 'SAIDA', 'AJUSTE'}:
            return jsonify({'ok': False, 'message': 'Tipo de movimentação inválido.'}), 400

        if _is_owner(usuario):
            return view_func(*args, **kwargs)
        if tipo == 'AJUSTE':
            return _forbidden_response()

        required_permissions = {'entradas'} if tipo == 'ENTRADA' else {'saidas', 'caixa'}
        if not _user_permissions(usuario).intersection(required_permissions):
            return _forbidden_response()
        return view_func(*args, **kwargs)
    return wrapped


def get_product_map():
    products = safe_query('produto', '*')
    lookup = {}
    for product in products:
        product_id = product.get('id_produto')
        if product_id is None:
            continue
        lookup[int(product_id)] = product
    return lookup


def get_product_by_id(product_id):
    if product_id in (None, ''):
        return None

    products = safe_query('produto', '*', id_produto=int(product_id))
    return products[0] if products else None


def get_product_price(product_id):
    product = get_product_by_id(product_id)
    if not product:
        return Decimal('0')

    try:
        return parse_decimal(product.get('preco', 0))
    except ValueError:
        return Decimal('0')


def decimal_money(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.01'))


def parse_date(raw_value):
    if raw_value in (None, ''):
        return None
    try:
        return datetime.strptime(str(raw_value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def format_date_br(value):
    return value.strftime('%d/%m/%Y') if value else ''


def lot_balance(lot):
    try:
        return parse_decimal(lot.get('quantidade', 0))
    except ValueError:
        return Decimal('0')


def get_validity_map(lots=None):
    """Menor validade, por produto, entre os lotes que ainda têm saldo."""
    validity = {}
    for lot in lots if lots is not None else safe_query('lote', '*'):
        product_id = lot.get('id_produto')
        expiry = parse_date(lot.get('data_validade'))
        if product_id is None or expiry is None or lot_balance(lot) <= 0:
            continue
        key = int(product_id)
        if key not in validity or expiry < validity[key]:
            validity[key] = expiry
    return validity


def build_product_alerts():
    alerts = []
    products = safe_query('produto', '*')
    product_map = {int(product.get('id_produto')): product for product in products if product.get('id_produto') is not None}
    stock_map = get_stock_map()

    for product in products:
        product_id = product.get('id_produto')
        if product_id is None or product.get('ativo') is False:
            continue

        try:
            stock = stock_map.get(int(product_id), Decimal('0'))
            minimum = parse_decimal(product.get('estoque_minimo', 0))
        except ValueError:
            continue

        if stock <= minimum:
            alerts.append({
                'id_produto': product_id,
                'tipo': 'ESTOQUE_BAIXO',
                'mensagem': f"Produto {product.get('nome', 'Sem nome')} com estoque abaixo do mínimo.",
                'produto': product.get('nome', 'Sem nome'),
                'quantidade': str(stock),
                'estoque_minimo': str(minimum)
            })

    lots = safe_query('lote', '*')
    today = hoje()
    for lot in lots:
        quantity = lot_balance(lot)
        expiry = parse_date(lot.get('data_validade'))
        if quantity <= 0 or expiry is None or lot.get('id_produto') is None:
            continue

        days_to_expiry = (expiry - today).days
        if days_to_expiry > DIAS_VALIDADE_PROXIMA:
            continue

        product = product_map.get(int(lot.get('id_produto')))
        product_name = product.get('nome', 'Produto') if product else 'Produto'
        if days_to_expiry < 0:
            alert_type = 'LOTE_VENCIDO'
            message = f'Lote de {product_name} venceu em {format_date_br(expiry)} e ainda tem saldo.'
        elif days_to_expiry == 0:
            alert_type = 'VALIDADE_PROXIMA'
            message = f'Lote de {product_name} vence hoje.'
        else:
            alert_type = 'VALIDADE_PROXIMA'
            plural = 'dia' if days_to_expiry == 1 else 'dias'
            message = f'Lote de {product_name} vence em {days_to_expiry} {plural}.'

        alerts.append({
            'id_lote': lot.get('id_lote'),
            'id_produto': lot.get('id_produto'),
            'tipo': alert_type,
            'mensagem': message,
            'produto': product_name,
            'quantidade': str(quantity),
            'validade': expiry.isoformat(),
            'dias_para_vencer': days_to_expiry
        })

    return alerts


def get_category_map():
    categories = safe_query('categoria', '*')
    lookup = {}
    for category in categories:
        category_id = category.get('id_categoria')
        if category_id is None:
            continue
        lookup[int(category_id)] = category
    return lookup


def get_category_name(category_id):
    if category_id in (None, ''):
        return 'Sem categoria'

    category = get_category_map().get(int(category_id))
    if category:
        return category.get('nome', 'Sem categoria')
    return 'Sem categoria'


def get_price_map():
    price_map = {}
    products = safe_query('produto', '*')

    for product in products:
        product_id = product.get('id_produto')
        if product_id is None:
            continue

        try:
            price_map[int(product_id)] = parse_decimal(product.get('preco', 0))
        except ValueError:
            price_map[int(product_id)] = Decimal('0')

    return price_map


def get_stock_map(lots=None):
    totals = {}
    if lots is None:
        lots = safe_query('lote', '*')

    for lot in lots:
        product_id = lot.get('id_produto')
        if product_id is None:
            continue

        try:
            quantidade = parse_decimal(lot.get('quantidade', 0))
        except ValueError:
            continue

        key = int(product_id)
        totals[key] = totals.get(key, Decimal('0')) + quantidade

    return totals


def calculate_product_stock(product_id):
    lots = safe_query('lote', '*', id_produto=int(product_id))
    total = Decimal('0')

    for lot in lots:
        try:
            total += parse_decimal(lot.get('quantidade', 0))
        except ValueError:
            continue

    return total


def plan_fefo_exit(product_id, quantity):
    """Plano de baixa FEFO: lista de (lote, quantidade usada), ou None sem saldo suficiente.

    Sai primeiro o lote que vence antes; lotes sem validade ficam por último.
    """
    remaining = parse_decimal(quantity)
    lots = safe_query('lote', '*', id_produto=int(product_id))
    lots = sorted(
        lots,
        key=lambda item: (str(item.get('data_validade') or '9999-12-31'), item.get('id_lote', 0))
    )

    plan = []
    for lot in lots:
        if remaining <= 0:
            break

        lot_quantity = lot_balance(lot)
        if lot_quantity <= 0:
            continue

        used = min(remaining, lot_quantity)
        plan.append((lot, used))
        remaining -= used

    if remaining > 0 or not plan:
        return None
    return plan


def normalize_exit_reason(raw_reason):
    reason = str(raw_reason or '').strip()
    if not reason:
        return 'Venda'
    for option in MOTIVOS_SAIDA:
        if reason.lower() == option.lower():
            return option
    return None


def build_exit_note(reason, note):
    return f'[{reason}] {str(note or "").strip()}'.strip()


def split_exit_note(note):
    """Separa o motivo gravado no início da observação: '[Perda] texto'."""
    match = MOTIVO_NA_OBSERVACAO.match(str(note or ''))
    if not match:
        return None, note
    return match.group(1), match.group(2)


def undo_exit(changed_lots, created_movements):
    for movement in created_movements:
        safe_delete('movimentacao', 'id_movimentacao', movement.get('id_movimentacao'))
    for lot_id, previous_quantity in changed_lots:
        atualizar_saldo_lote(lot_id, previous_quantity)


def gravar_plano_saida(product_id, plan, usuario, valor_unitario, observacao, data_movimentacao,
                       changed_lots, created_movements):
    """Baixa os lotes do plano FEFO e grava uma movimentação SAIDA por lote consumido.

    Vai anotando em changed_lots e created_movements o que gravou, para quem chamou
    poder desfazer tudo com undo_exit. Devolve False se alguma gravação falhar.
    """
    for lot, used in plan:
        lot_id = lot.get('id_lote')
        previous_quantity = lot_balance(lot)
        if not atualizar_saldo_lote(lot_id, previous_quantity - used):
            return False
        changed_lots.append((lot_id, previous_quantity))

        movimentacao = safe_insert('movimentacao', {
            'id_produto': int(product_id),
            'id_lote': lot_id,
            'id_usuario': usuario.get('id_usuario'),
            'tipo': 'SAIDA',
            'quantidade': str(used),
            'valor_total': str(decimal_money(used * valor_unitario)),
            'valor_unitario': str(decimal_money(valor_unitario)),
            'data_movimentacao': data_movimentacao,
            'observacao': observacao
        })
        if movimentacao is None:
            return False
        created_movements.append(movimentacao)
    return True


@app.get('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'database': 'connected' if supabase else 'not-configured'
    })


@app.post('/api/auth/login')
def login():
    payload = request.get_json(silent=True) or {}
    usuario = str(payload.get('usuario', '')).strip()
    senha = str(payload.get('senha', '')).strip()

    if not usuario or not senha:
        return jsonify({'ok': False, 'message': 'Usuário e senha são obrigatórios.'}), 400

    if not supabase:
        return jsonify({'ok': False, 'message': 'Banco não configurado.'}), 503

    serializer = _token_serializer()
    if serializer is None:
        return jsonify({'ok': False, 'message': 'Autenticação não configurada no servidor.'}), 503

    usuario_validado = get_user_by_login(usuario)

    if not usuario_validado:
        return jsonify({'ok': False, 'message': 'Credenciais inválidas.'}), 401

    senha_banco = str(usuario_validado.get('senha', '')).strip()
    hash_prefixes = ('scrypt:', 'pbkdf2:')
    if senha_banco.startswith(hash_prefixes):
        try:
            senha_valida = check_password_hash(senha_banco, senha)
        except (TypeError, ValueError):
            senha_valida = False
    else:
        senha_valida = senha_banco == senha
        if senha_valida:
            senha_hash = generate_password_hash(senha)
            if not save_user_password_hash(usuario_validado.get('id_usuario'), senha_hash):
                return jsonify({
                    'ok': False,
                    'message': 'Não foi possível atualizar a senha. Tente novamente.'
                }), 500

    if not senha_valida:
        return jsonify({'ok': False, 'message': 'Credenciais inválidas.'}), 401

    token = serializer.dumps({'id_usuario': usuario_validado.get('id_usuario')})
    permissoes = usuario_validado.get('permissoes') or []
    if isinstance(permissoes, str):
        permissoes = [permissoes]

    return jsonify({
        'ok': True,
        'token': token,
        'usuario': usuario_validado.get('login', usuario),
        'perfil': usuario_validado.get('tipo', 'FUNCIONARIO'),
        'permissoes': permissoes,
        'message': 'Login realizado com sucesso.'
    })


@app.get('/api/usuarios')
@login_required
@somente_dono
def list_users():
    if not supabase:
        return jsonify({'ok': False, 'message': 'Banco não configurado.'}), 503

    users = safe_query('usuario', '*')
    ordered_users = sorted(users, key=lambda user: str(user.get('nome') or '').lower())
    return jsonify({'items': [_public_user(user) for user in ordered_users]}), 200


@app.post('/api/usuarios')
@login_required
@somente_dono
def create_user():
    if not supabase:
        return jsonify({'ok': False, 'message': 'Banco não configurado.'}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'ok': False, 'message': 'Dados do usuário inválidos.'}), 400

    nome = str(payload.get('nome') or '').strip()
    login_name = str(payload.get('login') or '').strip()
    senha = str(payload.get('senha') or '').strip()
    tipo = str(payload.get('tipo', payload.get('perfil', 'FUNCIONARIO')) or 'FUNCIONARIO').strip().upper()

    if not nome or not login_name or not senha:
        return jsonify({'ok': False, 'message': 'Nome, usuário e senha são obrigatórios.'}), 400
    if tipo not in {'DONO', 'FUNCIONARIO'}:
        return jsonify({'ok': False, 'message': 'Perfil inválido.'}), 400

    default_permissions = None
    if 'permissoes' not in payload:
        default_permissions = (
            PERMISSOES_PADRAO_FUNCIONARIO if tipo == 'FUNCIONARIO' else TELAS_PERMITIDAS
        )
    try:
        permissions = _normalize_permissions(payload.get('permissoes'), default_permissions)
    except ValueError as error:
        return jsonify({'ok': False, 'message': str(error)}), 400

    existing_users = safe_query('usuario', '*')
    if any(str(user.get('login') or '').strip().lower() == login_name.lower() for user in existing_users):
        return jsonify({'ok': False, 'message': 'Este usuário já existe.'}), 409

    user_payload = {
        'nome': nome,
        'login': login_name,
        'senha': generate_password_hash(senha),
        'tipo': tipo,
        'permissoes': permissions,
        'ativo': True
    }
    created = safe_insert('usuario', user_payload)
    if created is None:
        return jsonify({'ok': False, 'message': 'Não foi possível criar o usuário.'}), 500

    return jsonify({
        'ok': True,
        'message': 'Usuário criado com sucesso.',
        'usuario': _public_user({**user_payload, **created})
    }), 201


@app.put('/api/usuarios/<int:user_id>')
@login_required
@somente_dono
def update_user(user_id):
    if not supabase:
        return jsonify({'ok': False, 'message': 'Banco não configurado.'}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'ok': False, 'message': 'Dados do usuário inválidos.'}), 400

    existing_users = safe_query('usuario', '*', id_usuario=user_id)
    if not existing_users:
        return jsonify({'ok': False, 'message': 'Usuário não encontrado.'}), 404

    existing = existing_users[0]
    current_user = g.usuario_autenticado
    is_self = str(current_user.get('id_usuario')) == str(user_id)
    update_payload = {}

    if 'nome' in payload:
        nome = str(payload.get('nome') or '').strip()
        if not nome:
            return jsonify({'ok': False, 'message': 'Nome é obrigatório.'}), 400
        update_payload['nome'] = nome

    tipo = str(payload.get('tipo', payload.get('perfil', existing.get('tipo', 'FUNCIONARIO')))).strip().upper()
    if tipo not in {'DONO', 'FUNCIONARIO'}:
        return jsonify({'ok': False, 'message': 'Perfil inválido.'}), 400
    if is_self and tipo != 'DONO':
        return jsonify({'ok': False, 'message': 'O Dono não pode remover o próprio perfil.'}), 400
    if 'tipo' in payload or 'perfil' in payload:
        update_payload['tipo'] = tipo

    if 'ativo' in payload:
        ativo = payload['ativo']
        if not isinstance(ativo, bool):
            return jsonify({'ok': False, 'message': 'Status ativo inválido.'}), 400
        if is_self and not ativo:
            return jsonify({'ok': False, 'message': 'O Dono não pode inativar a si mesmo.'}), 400
        update_payload['ativo'] = ativo

    if 'permissoes' in payload:
        try:
            update_payload['permissoes'] = _normalize_permissions(payload['permissoes'])
        except ValueError as error:
            return jsonify({'ok': False, 'message': str(error)}), 400

    senha = str(payload.get('senha') or '').strip()
    if senha:
        update_payload['senha'] = generate_password_hash(senha)

    if not update_payload:
        return jsonify({'ok': False, 'message': 'Nenhuma alteração informada.'}), 400

    try:
        response = (
            supabase.table('usuario')
            .update(update_payload)
            .eq('id_usuario', user_id)
            .execute()
        )
        updated = response.data[0] if response.data else {**existing, **update_payload}
    except Exception:
        return jsonify({'ok': False, 'message': 'Não foi possível atualizar o usuário.'}), 500

    limpar_cache_usuarios()
    return jsonify({
        'ok': True,
        'message': 'Usuário atualizado com sucesso.',
        'usuario': _public_user(updated)
    }), 200


@app.get('/api/categorias')
@login_required
@permissao_qualquer('produtos', 'entradas', 'saidas', 'caixa')
def list_categories():
    items = safe_query('categoria', '*')
    return jsonify({'items': items}), 200


@app.get('/api/fornecedores')
@login_required
@permissao_qualquer('fornecedores', 'entradas')
def list_suppliers():
    items = safe_query('fornecedor', '*')
    ordered = sorted(items, key=lambda x: str(x.get('nome') or '').lower())
    return jsonify({'items': ordered}), 200


TELEFONE_PERMITIDO = re.compile(r'^[\d\s().+-]+$')


def normalizar_nome_fornecedor(nome):
    """Sem espaços nas pontas, espaços repetidos viram um e tudo em minúsculas."""
    return ' '.join(str(nome or '').split()).casefold()


def digitos_telefone(telefone):
    return re.sub(r'\D', '', str(telefone or ''))


def normalizar_email(email):
    return str(email or '').strip().casefold()


def validar_telefone(telefone):
    """Telefone vazio é aceito; preenchido precisa ter 10 ou 11 dígitos (DDD + número)."""
    telefone = str(telefone or '').strip()
    if not telefone:
        return None
    if not TELEFONE_PERMITIDO.match(telefone) or len(digitos_telefone(telefone)) not in (10, 11):
        return 'Telefone inválido. Informe o DDD e o número, com 10 ou 11 dígitos.'
    return None


def fornecedor_repetido(nome, telefone, email, supplier_id=None, campos=('nome', 'telefone', 'email')):
    """Mensagem dizendo qual campo já existe em outro fornecedor (ativo ou inativo), ou None.

    O próprio fornecedor (supplier_id) não conta como repetido.
    """
    procurado = {
        'nome': normalizar_nome_fornecedor(nome),
        'telefone': digitos_telefone(telefone),
        'email': normalizar_email(email),
    }
    normalizar = {
        'nome': normalizar_nome_fornecedor,
        'telefone': digitos_telefone,
        'email': normalizar_email,
    }
    mensagens = {
        'nome': 'Já existe um fornecedor com este nome.',
        'telefone': 'Já existe um fornecedor com este telefone.',
        'email': 'Já existe um fornecedor com este e-mail.',
    }
    outros = [
        supplier for supplier in safe_query('fornecedor', '*')
        if str(supplier.get('id_fornecedor')) != str(supplier_id)
    ]
    for campo in ('nome', 'telefone', 'email'):
        if campo not in campos or not procurado[campo]:
            continue
        if any(normalizar[campo](supplier.get(campo)) == procurado[campo] for supplier in outros):
            return mensagens[campo]
    return None


@app.post('/api/fornecedores')
@login_required
@permissao('fornecedores')
def create_supplier():
    payload = request.get_json(silent=True) or {}
    nome = str(payload.get('nome', '')).strip()
    telefone = str(payload.get('telefone') or '').strip()
    email = str(payload.get('email') or '').strip()

    if not nome:
        return jsonify({'ok': False, 'message': 'Nome do fornecedor é obrigatório.'}), 400

    erro_telefone = validar_telefone(telefone)
    if erro_telefone:
        return jsonify({'ok': False, 'message': erro_telefone}), 400

    repetido = fornecedor_repetido(nome, telefone, email)
    if repetido:
        return jsonify({'ok': False, 'message': repetido}), 409

    supplier_payload = {
        'nome': nome,
        'telefone': telefone or None,
        'email': email or None,
        'ativo': bool(payload.get('ativo', True))
    }

    created = safe_insert('fornecedor', supplier_payload)
    if created is None:
        return jsonify({'ok': False, 'message': 'Não foi possível cadastrar o fornecedor.'}), 500

    return jsonify({
        'ok': True,
        'message': 'Fornecedor cadastrado com sucesso.',
        'id_fornecedor': created.get('id_fornecedor'),
        'nome': str(created.get('nome') or nome),
        'ativo': bool(created.get('ativo', True))
    }), 200


@app.put('/api/fornecedores/<int:supplier_id>')
@login_required
@permissao('fornecedores')
def update_supplier(supplier_id):
    existing = safe_query('fornecedor', '*', id_fornecedor=supplier_id)
    if not existing:
        return jsonify({'ok': False, 'message': 'Fornecedor não encontrado.'}), 404

    payload = request.get_json(silent=True) or {}
    nome = str(payload.get('nome', existing[0].get('nome', ''))).strip()
    if not nome:
        return jsonify({'ok': False, 'message': 'Nome do fornecedor é obrigatório.'}), 400

    telefone = str(payload.get('telefone') if payload.get('telefone') is not None else existing[0].get('telefone') or '').strip()
    email = str(payload.get('email') if payload.get('email') is not None else existing[0].get('email') or '').strip()

    # Só confere o que mudou: dados antigos já gravados (ex.: telefone fora do padrão)
    # não impedem editar ou inativar o fornecedor.
    atual = existing[0]
    mudou = {
        'nome': normalizar_nome_fornecedor(nome) != normalizar_nome_fornecedor(atual.get('nome')),
        'telefone': telefone != str(atual.get('telefone') or '').strip(),
        'email': normalizar_email(email) != normalizar_email(atual.get('email')),
    }

    if mudou['telefone']:
        erro_telefone = validar_telefone(telefone)
        if erro_telefone:
            return jsonify({'ok': False, 'message': erro_telefone}), 400

    repetido = fornecedor_repetido(
        nome, telefone, email, supplier_id,
        campos=[campo for campo, alterado in mudou.items() if alterado]
    )
    if repetido:
        return jsonify({'ok': False, 'message': repetido}), 409

    update_payload = {
        'nome': nome,
        'telefone': telefone or None,
        'email': email or None,
        'ativo': bool(payload.get('ativo', existing[0].get('ativo', True)))
    }

    try:
        response = supabase.table('fornecedor').update(update_payload).eq('id_fornecedor', supplier_id).execute()
        updated = (response.data or [{}])[0] if response.data else {**existing[0], **update_payload}
    except Exception:
        return jsonify({'ok': False, 'message': 'Não foi possível atualizar o fornecedor.'}), 500

    return jsonify({
        'ok': True,
        'message': 'Fornecedor atualizado com sucesso.',
        'id_fornecedor': supplier_id,
        'nome': str(updated.get('nome') or nome),
        'ativo': bool(updated.get('ativo', True))
    }), 200


@app.get('/api/produtos')
@login_required
@permissao_qualquer('produtos', 'entradas', 'saidas', 'caixa', 'painel', 'relatorios')
def list_products():
    buscar_tabelas('produto', 'categoria', 'lote')
    items = safe_query('produto', '*')
    search = str(request.args.get('search') or '').strip().lower()

    if search:
        filtered = []
        for item in items:
            nome = str(item.get('nome') or '').lower()
            codigo = str(item.get('codigo_barras') or '').lower()
            id_texto = str(item.get('id_produto') or '').lower()
            if search in nome or search in codigo or search in id_texto:
                filtered.append(item)
        items = filtered

    categories = get_category_map()
    lots = safe_query('lote', '*')
    stock_map = get_stock_map(lots)
    validity_map = get_validity_map(lots)
    price_map = get_price_map()
    today = hoje()
    enriched = []

    for item in items:
        product_id = item.get('id_produto')
        if product_id is None:
            continue

        product_key = int(product_id)
        item_copy = dict(item)
        next_expiry = validity_map.get(product_key)
        item_copy['data_validade'] = next_expiry.isoformat() if next_expiry else None
        item_copy['alerta_validade'] = bool(
            next_expiry and (next_expiry - today).days <= DIAS_VALIDADE_PROXIMA
        )
        category_id = item_copy.get('id_categoria')
        item_copy['categoria'] = categories.get(int(category_id), {}).get('nome', 'Sem categoria') if category_id not in (None, '') else 'Sem categoria'

        try:
            item_copy['estoque_atual'] = str(stock_map.get(product_key, Decimal('0')))
        except Exception:
            item_copy['estoque_atual'] = '0'

        try:
            item_copy['preco'] = str(decimal_money(price_map.get(product_key, Decimal('0'))))
        except Exception:
            item_copy['preco'] = '0.00'
        enriched.append(item_copy)

    return jsonify({'items': enriched}), 200


@app.post('/api/produtos')
@login_required
@permissao('produtos')
def create_product():
    payload = request.get_json(silent=True) or {}

    nome = str(payload.get('nome', '')).strip()
    categoria_id = payload.get('id_categoria')
    unidade = str(payload.get('unidade_medida') or '').strip()

    if not nome or not categoria_id or not unidade:
        return jsonify({'ok': False, 'message': 'Nome, categoria e unidade são obrigatórios.'}), 400

    codigo_barras = str(payload.get('codigo_barras') or '').strip() or None
    if codigo_barras:
        existing = safe_query('produto', '*', codigo_barras=codigo_barras)
        if existing:
            return jsonify({'ok': False, 'message': 'Código de barras já cadastrado.'}), 409

    try:
        estoque_minimo = parse_decimal(payload.get('estoque_minimo', 0))
        preco = parse_decimal(payload.get('preco', 0))
    except ValueError:
        return jsonify({'ok': False, 'message': 'Estoque mínimo ou preço inválido.'}), 400

    product_payload = {
        'id_categoria': int(categoria_id),
        'nome': nome,
        'codigo_barras': codigo_barras,
        'unidade_medida': unidade,
        'estoque_minimo': str(estoque_minimo),
        'preco': str(decimal_money(preco)),
        'ativo': bool(payload.get('ativo', True))
    }

    created = safe_insert('produto', product_payload)
    if created is None:
        return jsonify({'ok': False, 'message': 'Não foi possível cadastrar o produto.'}), 500

    payload_response = {'ok': True, 'message': 'Produto cadastrado com sucesso.', 'id_produto': created.get('id_produto'), 'preco': str(decimal_money(created.get('preco', preco))), 'nome': nome}
    return jsonify(payload_response), 200


@app.put('/api/produtos/<int:product_id>')
@login_required
@permissao('produtos')
def update_product(product_id):
    product = get_product_by_id(product_id)
    if not product:
        return jsonify({'ok': False, 'message': 'Produto não encontrado.'}), 404

    payload = request.get_json(silent=True) or {}
    nome = str(payload.get('nome', product.get('nome', ''))).strip()
    categoria_id = payload.get('id_categoria', product.get('id_categoria'))
    unidade = str(payload.get('unidade_medida') or product.get('unidade_medida') or '').strip()

    if not nome or not categoria_id or not unidade:
        return jsonify({'ok': False, 'message': 'Nome, categoria e unidade são obrigatórios.'}), 400

    codigo_barras = str(payload.get('codigo_barras') or product.get('codigo_barras') or '').strip() or None
    if codigo_barras:
        existing = safe_query('produto', '*', codigo_barras=codigo_barras)
        if existing and existing[0].get('id_produto') != product_id:
            return jsonify({'ok': False, 'message': 'Código de barras já cadastrado.'}), 409

    try:
        estoque_minimo = parse_decimal(payload.get('estoque_minimo', product.get('estoque_minimo', 0)))
        preco = parse_decimal(payload.get('preco', product.get('preco', 0)))
    except ValueError:
        return jsonify({'ok': False, 'message': 'Estoque mínimo ou preço inválido.'}), 400

    update_payload = {
        'id_categoria': int(categoria_id),
        'nome': nome,
        'codigo_barras': codigo_barras,
        'unidade_medida': unidade,
        'estoque_minimo': str(estoque_minimo),
        'preco': str(decimal_money(preco)),
        'ativo': bool(payload.get('ativo', product.get('ativo', True)))
    }

    try:
        response = supabase.table('produto').update(update_payload).eq('id_produto', product_id).execute()
        updated = (response.data or [{}])[0] if response.data else {**product, **update_payload}
    except Exception:
        return jsonify({'ok': False, 'message': 'Não foi possível atualizar o produto.'}), 500

    return jsonify({
        'ok': True,
        'message': 'Produto atualizado com sucesso.',
        'id_produto': product_id,
        'nome': str(updated.get('nome') or nome),
        'preco': str(decimal_money(updated.get('preco', preco)))
    }), 200


@app.delete('/api/produtos/<int:product_id>')
@login_required
@permissao('produtos')
def delete_product(product_id):
    product = get_product_by_id(product_id)
    if not product:
        return jsonify({'ok': False, 'message': 'Produto não encontrado.'}), 404

    try:
        supabase.table('produto').update({'ativo': False}).eq('id_produto', product_id).execute()
    except Exception:
        return jsonify({'ok': False, 'message': 'Não foi possível inativar o produto.'}), 500

    return jsonify({
        'ok': True,
        'message': 'Produto inativado com sucesso.',
        'id_produto': product_id,
        'ativo': False
    }), 200


@app.get('/api/movimentacoes')
@login_required
@permissao_qualquer('entradas', 'saidas', 'relatorios', 'painel')
def list_movements():
    buscar_tabelas('movimentacao', 'produto', 'categoria', 'lote', 'fornecedor')
    items = safe_query('movimentacao', '*')
    usuario = g.usuario_autenticado
    permissions = _user_permissions(usuario)
    if not _is_owner(usuario) and not permissions.intersection({'relatorios', 'painel'}):
        allowed_types = set()
        if 'entradas' in permissions:
            allowed_types.add('ENTRADA')
        if 'saidas' in permissions:
            allowed_types.add('SAIDA')
        items = [item for item in items if str(item.get('tipo') or '').upper() in allowed_types]

    product_map = get_product_map()
    category_map = get_category_map()
    # O fornecedor fica no lote: a movimentação chega a ele pelo id_lote.
    lot_map = {
        str(lot.get('id_lote')): lot for lot in safe_query('lote', '*') if lot.get('id_lote') is not None
    }
    supplier_map = {
        str(supplier.get('id_fornecedor')): supplier
        for supplier in safe_query('fornecedor', '*') if supplier.get('id_fornecedor') is not None
    }
    enriched = []

    for item in items:
        item_copy = dict(item)
        product_id = item_copy.get('id_produto')
        product = product_map.get(int(product_id)) if product_id not in (None, '') else None
        item_copy['produto'] = product.get('nome', 'Produto') if product else 'Produto'
        category_id = product.get('id_categoria') if product else None
        category = category_map.get(int(category_id)) if category_id not in (None, '') else None
        item_copy['categoria'] = category.get('nome', 'Sem categoria') if category else 'Sem categoria'

        lot = lot_map.get(str(item_copy.get('id_lote')))
        supplier_id = lot.get('id_fornecedor') if lot else None
        supplier = supplier_map.get(str(supplier_id)) if supplier_id not in (None, '') else None
        item_copy['id_fornecedor'] = supplier_id
        item_copy['fornecedor'] = supplier.get('nome') if supplier else None

        if str(item_copy.get('tipo') or '').upper() == 'SAIDA':
            reason, note = split_exit_note(item_copy.get('observacao'))
            if reason:
                item_copy['motivo'] = reason
                item_copy['observacao'] = note

        try:
            # Vale o valor gravado na movimentação (preço da época); o preço atual
            # do produto só é usado quando a movimentação não tem valor gravado.
            quantidade = parse_decimal(item_copy.get('quantidade', 0))
            current_price = parse_decimal(product.get('preco', 0)) if product else Decimal('0')
            saved_unit = item_copy.get('valor_unitario')
            saved_total = item_copy.get('valor_total')
            valor_unitario = parse_decimal(saved_unit) if saved_unit not in (None, '') else current_price
            if saved_total not in (None, ''):
                total = decimal_money(parse_decimal(saved_total))
            else:
                total = decimal_money(quantidade * valor_unitario)
            item_copy['valor_unitario'] = str(decimal_money(valor_unitario))
            item_copy['valor_total'] = str(total)
        except Exception:
            item_copy['valor_unitario'] = '0.00'
            item_copy['valor_total'] = '0.00'

        enriched.append(item_copy)

    return jsonify({'items': enriched}), 200


@app.delete('/api/movimentacoes/<int:movement_id>')
@login_required
@somente_dono
def delete_movement(movement_id):
    if not supabase:
        return jsonify({'ok': False, 'message': 'Banco não configurado.'}), 503

    movements = safe_query('movimentacao', '*', id_movimentacao=movement_id)
    if not movements:
        return jsonify({'ok': False, 'message': 'Movimentação não encontrada.'}), 404

    movement = movements[0]
    tipo = str(movement.get('tipo') or '').upper()
    try:
        quantidade = parse_decimal(movement.get('quantidade', 0))
    except ValueError:
        quantidade = Decimal('0')

    lot_id = movement.get('id_lote')
    lots = safe_query('lote', '*', id_lote=int(lot_id)) if lot_id not in (None, '') else []
    lot = lots[0] if lots else None
    failure = jsonify({'ok': False, 'message': 'Não foi possível excluir a movimentação.'}), 500

    if tipo == 'ENTRADA' and lot:
        # A entrada criou o lote: só pode ser desfeita se nada saiu dele ainda.
        other_movements = [
            item for item in safe_query('movimentacao', '*', id_lote=int(lot_id))
            if str(item.get('id_movimentacao')) != str(movement_id)
        ]
        if other_movements:
            return jsonify({
                'ok': False,
                'message': 'Este lote já tem saídas registradas. Exclua essas saídas antes de excluir a entrada.'
            }), 409
        if not safe_delete('movimentacao', 'id_movimentacao', movement_id):
            return failure
        if not safe_delete('lote', 'id_lote', int(lot_id)):
            atualizar_saldo_lote(int(lot_id), Decimal('0'))
        return jsonify({
            'ok': True,
            'message': 'Entrada excluída. O lote foi removido do estoque.'
        }), 200

    if tipo == 'SAIDA' and lot:
        # Estorno: a quantidade volta para o lote de onde saiu.
        previous_quantity = lot_balance(lot)
        if not atualizar_saldo_lote(int(lot_id), previous_quantity + quantidade):
            return failure
        if not safe_delete('movimentacao', 'id_movimentacao', movement_id):
            atualizar_saldo_lote(int(lot_id), previous_quantity)
            return failure
        return jsonify({
            'ok': True,
            'message': 'Saída excluída. A quantidade voltou para o estoque.'
        }), 200

    if not safe_delete('movimentacao', 'id_movimentacao', movement_id):
        return failure
    return jsonify({'ok': True, 'message': 'Movimentação excluída com sucesso.'}), 200


@app.post('/api/movimentacoes')
@login_required
@permissao_movimentacao
def create_movement():
    payload = request.get_json(silent=True) or {}
    tipo = str(payload.get('tipo', 'ENTRADA')).upper().strip()
    product_id = payload.get('id_produto')

    if product_id in (None, ''):
        return jsonify({'ok': False, 'message': 'Produto obrigatório.'}), 400

    product = get_product_by_id(product_id)
    if not product:
        return jsonify({'ok': False, 'message': 'Produto não encontrado.'}), 404
    if tipo in ('ENTRADA', 'SAIDA') and product.get('ativo') is False:
        return jsonify({'ok': False, 'message': 'Produto inativo.'}), 400

    usuario = g.usuario_autenticado

    try:
        quantidade = parse_decimal(payload.get('quantidade', 0))
    except ValueError:
        return jsonify({'ok': False, 'message': 'Quantidade inválida.'}), 400

    if quantidade <= 0:
        return jsonify({'ok': False, 'message': 'Quantidade deve ser maior que zero.'}), 400

    valor_unitario = get_product_price(product_id)
    valor_total = decimal_money(quantidade * valor_unitario)

    if tipo == 'ENTRADA':
        fornecedor_id = payload.get('id_fornecedor')
        if fornecedor_id in (None, ''):
            return jsonify({'ok': False, 'message': 'Fornecedor obrigatório para entrada.'}), 400
        try:
            fornecedor_id = int(fornecedor_id)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'message': 'Fornecedor inválido.'}), 400

        data_entrada = parse_date(payload.get('data_entrada')) if payload.get('data_entrada') else hoje()
        if data_entrada is None:
            return jsonify({'ok': False, 'message': 'Data de entrada inválida.'}), 400

        data_validade = None
        if payload.get('data_validade'):
            data_validade = parse_date(payload.get('data_validade'))
            if data_validade is None:
                return jsonify({'ok': False, 'message': 'Data de validade inválida.'}), 400

        lote_payload = {
            'id_produto': int(product_id),
            'id_fornecedor': fornecedor_id,
            'data_entrada': data_entrada.isoformat(),
            'data_validade': data_validade.isoformat() if data_validade else None,
            'quantidade': str(quantidade)
        }

        lote = safe_insert('lote', lote_payload)
        if lote is None:
            return jsonify({'ok': False, 'message': 'Não foi possível registrar o lote.'}), 500

        movimentacao = safe_insert('movimentacao', {
            'id_produto': int(product_id),
            'id_lote': lote.get('id_lote'),
            'id_usuario': usuario.get('id_usuario'),
            'tipo': 'ENTRADA',
            'quantidade': str(quantidade),
            'valor_total': str(valor_total),
            'valor_unitario': str(decimal_money(valor_unitario)),
            'data_movimentacao': agora().isoformat(timespec='seconds'),
            'observacao': str(payload.get('observacao') or '').strip()
        })
        if movimentacao is None:
            # Sem a movimentação, o lote não pode ficar sozinho no estoque.
            if not safe_delete('lote', 'id_lote', lote.get('id_lote')):
                atualizar_saldo_lote(lote.get('id_lote'), Decimal('0'))
            return jsonify({'ok': False, 'message': 'Não foi possível registrar a entrada. Nada foi gravado.'}), 500

        return jsonify({'ok': True, 'message': 'Entrada registrada com sucesso.', 'id_lote': lote.get('id_lote'), 'id_movimentacao': movimentacao.get('id_movimentacao'), 'valor_total': str(valor_total)}), 200

    if tipo == 'SAIDA':
        motivo = normalize_exit_reason(payload.get('motivo'))
        if motivo is None:
            return jsonify({'ok': False, 'message': 'Motivo de saída inválido.'}), 400

        estoque_atual = calculate_product_stock(product_id)
        if quantidade > estoque_atual:
            return jsonify({'ok': False, 'message': 'Quantidade acima do disponível no estoque.'}), 400

        plan = plan_fefo_exit(product_id, quantidade)
        if plan is None:
            return jsonify({'ok': False, 'message': 'Quantidade acima do disponível no estoque.'}), 400

        observacao = build_exit_note(motivo, payload.get('observacao'))
        data_movimentacao = agora().isoformat(timespec='seconds')
        changed_lots = []
        created_movements = []

        if not gravar_plano_saida(
            product_id, plan, usuario, valor_unitario, observacao, data_movimentacao,
            changed_lots, created_movements
        ):
            undo_exit(changed_lots, created_movements)
            return jsonify({'ok': False, 'message': 'Não foi possível registrar a saída. Nada foi gravado.'}), 500

        return jsonify({
            'ok': True,
            'message': 'Saída registrada com sucesso.',
            'motivo': motivo,
            'id_lote': plan[-1][0].get('id_lote'),
            'id_movimentacao': created_movements[0].get('id_movimentacao'),
            'ids_movimentacao': [item.get('id_movimentacao') for item in created_movements],
            'valor_total': str(valor_total)
        }), 200

    return jsonify({'ok': False, 'message': 'Tipo de movimentação inválido.'}), 400


@app.post('/api/vendas')
@login_required
@permissao_qualquer('caixa', 'saidas')
def create_sale():
    """Registra a venda do caixa inteira de uma vez: ou grava todos os itens, ou nenhum."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'ok': False, 'message': 'Dados da venda inválidos.'}), 400

    forma_pagamento = str(payload.get('forma_pagamento') or 'DINHEIRO').strip().upper()
    if forma_pagamento not in FORMAS_PAGAMENTO:
        return jsonify({'ok': False, 'message': 'Forma de pagamento inválida.'}), 400

    itens = payload.get('itens')
    if not isinstance(itens, list) or not itens:
        return jsonify({'ok': False, 'message': 'Adicione ao menos um item à venda.'}), 400

    # Junta itens repetidos do mesmo produto para conferir o estoque pelo total.
    quantidades = {}
    for item in itens:
        if not isinstance(item, dict):
            return jsonify({'ok': False, 'message': 'Item da venda inválido.'}), 400
        try:
            product_id = int(item.get('id_produto'))
            quantidade = parse_decimal(item.get('quantidade', 0))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'message': 'Item da venda inválido.'}), 400
        if quantidade <= 0:
            return jsonify({'ok': False, 'message': 'Quantidade deve ser maior que zero.'}), 400
        quantidades[product_id] = quantidades.get(product_id, Decimal('0')) + quantidade

    # Primeiro confere TODOS os itens; só depois grava alguma coisa.
    planos = []
    for product_id, quantidade in quantidades.items():
        product = get_product_by_id(product_id)
        if not product:
            return jsonify({'ok': False, 'message': 'Produto não encontrado.'}), 404
        if product.get('ativo') is False:
            return jsonify({'ok': False, 'message': 'Produto inativo.'}), 400
        nome = product.get('nome') or 'Produto'
        plan = plan_fefo_exit(product_id, quantidade)
        if plan is None:
            return jsonify({
                'ok': False,
                'message': f'Estoque insuficiente para {nome}. Nada foi gravado.'
            }), 400
        try:
            valor_unitario = parse_decimal(product.get('preco', 0))
        except ValueError:
            valor_unitario = Decimal('0')
        planos.append((product_id, quantidade, plan, valor_unitario))

    usuario = g.usuario_autenticado
    observacao = build_exit_note('Venda', f'Venda via caixa - {forma_pagamento}')
    data_movimentacao = agora().isoformat(timespec='seconds')
    changed_lots = []
    created_movements = []
    valor_total = Decimal('0')

    for product_id, quantidade, plan, valor_unitario in planos:
        if not gravar_plano_saida(
            product_id, plan, usuario, valor_unitario, observacao, data_movimentacao,
            changed_lots, created_movements
        ):
            undo_exit(changed_lots, created_movements)
            return jsonify({'ok': False, 'message': 'Não foi possível registrar a venda. Nada foi gravado.'}), 500
        valor_total += decimal_money(quantidade * valor_unitario)

    return jsonify({
        'ok': True,
        'message': 'Venda concluída com sucesso.',
        'forma_pagamento': forma_pagamento,
        'ids_movimentacao': [item.get('id_movimentacao') for item in created_movements],
        'valor_total': str(decimal_money(valor_total))
    }), 200


@app.get('/api/lotes')
@login_required
@permissao_qualquer('painel', 'entradas', 'saidas', 'caixa', 'relatorios')
def list_lots():
    buscar_tabelas('lote', 'produto')
    items = safe_query('lote', '*')
    product_map = get_product_map()
    enriched = []

    for item in items:
        item_copy = dict(item)
        product_id = item_copy.get('id_produto')
        product = product_map.get(int(product_id)) if product_id not in (None, '') else None
        item_copy['produto'] = product.get('nome', 'Produto') if product else 'Produto'
        enriched.append(item_copy)

    return jsonify({'items': enriched}), 200


@app.get('/api/alertas')
@login_required
@permissao_qualquer('painel', 'relatorios')
def list_alerts():
    buscar_tabelas('produto', 'lote')
    generated = build_product_alerts()
    return jsonify({'items': generated}), 200


@app.get('/api/relatorios')
@login_required
@permissao('relatorios')
def list_reports():
    buscar_tabelas('produto', 'lote')
    alerts = build_product_alerts()
    products = safe_query('produto', '*')
    stock_map = get_stock_map()
    low_stock_count = 0
    near_expiry_count = 0
    expired_count = 0

    for product in products:
        product_id = product.get('id_produto')
        if product_id is None or product.get('ativo') is False:
            continue
        try:
            stock = stock_map.get(int(product_id), Decimal('0'))
            minimum = parse_decimal(product.get('estoque_minimo', 0))
            if stock <= minimum:
                low_stock_count += 1
        except ValueError:
            continue

    for alert in alerts:
        if alert.get('tipo') == 'VALIDADE_PROXIMA':
            near_expiry_count += 1
        elif alert.get('tipo') == 'LOTE_VENCIDO':
            expired_count += 1

    return jsonify({
        'summary': {
            'produtos': len(products),
            'alertas': len(alerts),
            'estoque_baixo': low_stock_count,
            'validade_proxima': near_expiry_count,
            'lotes_vencidos': expired_count,
            'dias_validade_proxima': DIAS_VALIDADE_PROXIMA
        },
        'items': alerts
    })


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, debug=False, use_reloader=False)
