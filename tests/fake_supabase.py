"""Banco em memória que imita o cliente do Supabase usado pelo app.py.

Permite testar as regras de estoque sem tocar no banco compartilhado da equipe.
Suporta as chamadas usadas pelo app: select/eq, insert, update/eq e delete/eq.
"""

from types import SimpleNamespace

PRIMARY_KEYS = {
    'usuario': 'id_usuario',
    'categoria': 'id_categoria',
    'produto': 'id_produto',
    'fornecedor': 'id_fornecedor',
    'lote': 'id_lote',
    'movimentacao': 'id_movimentacao',
    'alerta': 'id_alerta',
}


class FakeQuery:
    def __init__(self, database, table_name):
        self.database = database
        self.table_name = table_name
        self.action = 'select'
        self.payload = None
        self.filters = []

    def select(self, _columns='*'):
        self.action = 'select'
        return self

    def insert(self, payload):
        self.action = 'insert'
        self.payload = payload
        return self

    def update(self, payload):
        self.action = 'update'
        self.payload = payload
        return self

    def delete(self):
        self.action = 'delete'
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def _matches(self, row):
        return all(str(row.get(column)) == str(value) for column, value in self.filters)

    def execute(self):
        self.database.check_failure(self.table_name, self.action)
        rows = self.database.tables.setdefault(self.table_name, [])

        if self.action == 'select':
            return SimpleNamespace(data=[dict(row) for row in rows if self._matches(row)])

        if self.action == 'insert':
            primary_key = PRIMARY_KEYS[self.table_name]
            row = dict(self.payload)
            row.setdefault(primary_key, self.database.next_id(self.table_name))
            rows.append(row)
            return SimpleNamespace(data=[dict(row)])

        if self.action == 'update':
            updated = []
            for row in rows:
                if self._matches(row):
                    row.update(self.payload)
                    updated.append(dict(row))
            return SimpleNamespace(data=updated)

        removed = [dict(row) for row in rows if self._matches(row)]
        self.database.tables[self.table_name] = [row for row in rows if not self._matches(row)]
        return SimpleNamespace(data=removed)


class FakeSupabase:
    def __init__(self, **tables):
        self.tables = {name: [dict(row) for row in rows] for name, rows in tables.items()}
        self.counters = {}
        self.failures = []

    def table(self, table_name):
        return FakeQuery(self, table_name)

    def next_id(self, table_name):
        primary_key = PRIMARY_KEYS[table_name]
        current = max(
            [int(row.get(primary_key) or 0) for row in self.tables.get(table_name, [])] + [self.counters.get(table_name, 0)]
        )
        self.counters[table_name] = current + 1
        return current + 1

    def fail_on(self, table_name, action, after=0):
        """Faz a operação falhar depois de `after` chamadas bem-sucedidas."""
        self.failures.append({'table': table_name, 'action': action, 'remaining': after})

    def check_failure(self, table_name, action):
        for failure in self.failures:
            if failure['table'] == table_name and failure['action'] == action:
                if failure['remaining'] == 0:
                    self.failures.remove(failure)
                    raise RuntimeError(f'Falha simulada em {action} de {table_name}')
                failure['remaining'] -= 1

    def rows(self, table_name):
        return self.tables.get(table_name, [])
