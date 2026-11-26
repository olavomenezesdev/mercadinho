import apiClient, { escapeHtml } from '../services/api/client.js';
import './session.js';

document.addEventListener('DOMContentLoaded', async () => {
  const API_BASE_URL = window.location.origin || 'http://127.0.0.1:8000';

  const moneyFormat = (value) =>
    new Intl.NumberFormat('pt-BR', {
      style: 'currency',
      currency: 'BRL',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(Number(value || 0));

  const numberFormat = (value) =>
    new Intl.NumberFormat('pt-BR').format(Number(value || 0));

  // Data no fuso do navegador (horário de Brasília), não em UTC.
  const localDateKey = (date) => [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0')
  ].join('-');

  const formatDateBR = (isoDate) => {
    const [year, month, day] = String(isoDate || '').slice(0, 10).split('-');
    return year && month && day ? `${day}/${month}/${year}` : '—';
  };

  const ALERT_LABELS = {
    ESTOQUE_BAIXO: 'Estoque baixo',
    VALIDADE_PROXIMA: 'Validade próxima',
    LOTE_VENCIDO: 'Lote vencido'
  };

  const getLast7Days = () => {
    return Array.from({ length: 7 }, (_, index) => {
      const date = new Date();
      date.setHours(0, 0, 0, 0);
      date.setDate(date.getDate() - (6 - index));
      return date;
    });
  };

  const formatDay = (date) =>
    new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit' }).format(date);

  const renderReports = async () => {
    try {
      const [productsResponse, movementsResponse, alertsResponse] = await Promise.all([
        apiClient.fetch(`${API_BASE_URL}/api/produtos`),
        apiClient.fetch(`${API_BASE_URL}/api/movimentacoes`),
        apiClient.fetch(`${API_BASE_URL}/api/alertas`)
      ]);

      const productsPayload = await productsResponse.json();
      const movementsPayload = await movementsResponse.json();
      const alertsPayload = await alertsResponse.json();

      // A tela usa sempre todos os dados (não há filtros nos relatórios).
      const products = Array.isArray(productsPayload?.items) ? productsPayload.items : [];
      const movements = Array.isArray(movementsPayload?.items) ? movementsPayload.items : [];
      const alerts = Array.isArray(alertsPayload?.items) ? alertsPayload.items : [];

      // Vendas = saídas com motivo Venda (perdas, vencimentos e avarias não contam como venda).
      const salesMovements = movements.filter((item) => (
        String(item.tipo || '').toUpperCase() === 'SAIDA' && (item.motivo || 'Venda') === 'Venda'
      ));
      const entriesMovements = movements.filter((item) => String(item.tipo || '').toUpperCase() === 'ENTRADA');

      const totalSalesUnits = salesMovements.reduce((sum, item) => sum + Number(item.quantidade || 0), 0);
      const totalRevenue = salesMovements.reduce((sum, item) => {
        const value = Number(item.valor_total || item.valor || 0);
        return sum + value;
      }, 0);
      const productsSold = new Set(salesMovements.map((item) => item.id_produto)).size;
      const avgTicket = salesMovements.length ? totalRevenue / salesMovements.length : 0;
      const lowStockAlerts = products.filter((item) => (
        item.ativo !== false && Number(item.estoque_atual || 0) <= Number(item.estoque_minimo || 0)
      ));

      const statCards = document.querySelectorAll('.stat-card');
      if (statCards.length >= 4) {
        statCards[0].querySelector('.stat-value').textContent = moneyFormat(totalRevenue);
        statCards[1].querySelector('.stat-value').textContent = numberFormat(productsSold || totalSalesUnits);
        statCards[2].querySelector('.stat-value').textContent = moneyFormat(avgTicket);
        statCards[3].querySelector('.stat-value').textContent = numberFormat(lowStockAlerts.length);

        const tags = statCards[0].querySelector('.mini-tag');
        if (tags) tags.textContent = `${numberFormat(totalSalesUnits)} itens`;
        statCards[1].querySelector('.mini-tag').textContent = `${numberFormat(products.length)} itens`;
        statCards[2].querySelector('.mini-tag').textContent = `${numberFormat(salesMovements.length)} itens vendidos`;
        statCards[3].querySelector('.mini-tag').textContent = lowStockAlerts.length ? 'Atenção' : 'Sem alerta';
        statCards[3].classList.toggle('warning', lowStockAlerts.length > 0);
      }

      const salesChart = document.getElementById('sales-chart');
      if (salesChart) {
        const sevenDays = getLast7Days();
        const maxValue = Math.max(1, ...sevenDays.map((date) => {
          const key = localDateKey(date);
          return salesMovements.filter((item) => {
            const movementDate = new Date(item.data_movimentacao || item.data || new Date());
            return localDateKey(movementDate) === key;
          }).reduce((sum, item) => sum + Number(item.quantidade || 0), 0);
        }));

        salesChart.innerHTML = sevenDays.map((date) => {
          const key = localDateKey(date);
          const value = salesMovements.filter((item) => {
            const movementDate = new Date(item.data_movimentacao || item.data || new Date());
            return localDateKey(movementDate) === key;
          }).reduce((sum, item) => sum + Number(item.quantidade || 0), 0);

          const height = Math.max(14, (value / maxValue) * 100);
          return `
            <div class="bar-group">
              <span class="bar-value">${escapeHtml(value)}</span>
              <div class="bar" style="height:${height}%"></div>
              <span class="bar-label">${escapeHtml(formatDay(date))}</span>
            </div>
          `;
        }).join('');
      }

      const categoryLegend = document.getElementById('category-legend');
      const donutChart = document.getElementById('donut-chart');
      const donutCenter = document.getElementById('donut-center');
      if (categoryLegend && donutChart && donutCenter) {
        const categoryTotals = products.reduce((acc, product) => {
          const categoryName = product.categoria || 'Sem categoria';
          acc[categoryName] = (acc[categoryName] || 0) + 1;
          return acc;
        }, {});

        const entries = Object.entries(categoryTotals).sort((a, b) => b[1] - a[1]).slice(0, 5);
        const total = entries.reduce((sum, [, value]) => sum + value, 0) || 1;
        const palette = ['#D9480F', '#EDA86B', '#F4C389', '#7A9D6E', '#D9D3C8'];
        let cursor = 0;
        const gradient = entries.map(([, value], index) => {
          const start = cursor;
          const end = cursor + (value / total) * 100;
          cursor = end;
          return `${palette[index % palette.length]} ${start}% ${end}%`;
        }).join(', ');

        donutChart.style.background = `conic-gradient(${gradient})`;
        donutCenter.innerHTML = `<strong>${total}</strong><span>produtos</span>`;
        categoryLegend.innerHTML = entries.map(([name, value], index) => `
          <li>
            <span style="display:inline-flex;align-items:center;gap:8px;"><span class="dot" style="background:${palette[index % palette.length]};"></span>${escapeHtml(name)}</span>
            <strong>${Math.round((value / total) * 100)}%</strong>
          </li>
        `).join('');
      }

      const expirationTableBody = document.getElementById('expiration-table-body');
      if (expirationTableBody) {
        // Mesma regra do servidor: lotes com saldo vencidos ou dentro do prazo de aviso.
        const upcoming = (Array.isArray(alertsPayload?.items) ? alertsPayload.items : [])
          .filter((alert) => alert.tipo === 'VALIDADE_PROXIMA' || alert.tipo === 'LOTE_VENCIDO')
          .sort((a, b) => Number(a.dias_para_vencer ?? 0) - Number(b.dias_para_vencer ?? 0))
          .slice(0, 5);

        if (!upcoming.length) {
          expirationTableBody.innerHTML = `
            <tr>
              <td colspan="4" class="empty-state-row">Nenhuma validade próxima.</td>
            </tr>
          `;
        } else {
          expirationTableBody.innerHTML = upcoming.map((lot) => {
            const productName = lot.produto || 'Produto';
            return `
              <tr>
                <td>
                  <span class="product-cell">
                    <span class="thumb thumb-1"></span>
                    ${escapeHtml(productName)}
                  </span>
                </td>
                <td>${escapeHtml(lot.id_lote || 'Lote')}</td>
                <td>${escapeHtml(formatDateBR(lot.validade))}${lot.tipo === 'LOTE_VENCIDO' ? ' (vencido)' : ''}</td>
                <td>${numberFormat(lot.quantidade)}</td>
              </tr>
            `;
          }).join('');
        }
      }

      const movementSummary = document.getElementById('movement-summary');
      if (movementSummary) {
        const entries = entriesMovements.reduce((sum, item) => sum + Number(item.quantidade || 0), 0);
        const exits = salesMovements.reduce((sum, item) => sum + Number(item.quantidade || 0), 0);
        const balance = entries - exits;

        movementSummary.innerHTML = `
          <div class="movement-item">
            <div class="movement-icon green">↑</div>
            <div class="movement-copy">
              <strong>Entradas</strong>
              <span>${numberFormat(entries)}</span>
            </div>
            <em>unidades</em>
          </div>
          <div class="movement-item">
            <div class="movement-icon red">↓</div>
            <div class="movement-copy">
              <strong>Vendas</strong>
              <span>${numberFormat(exits)}</span>
            </div>
            <em>unidades</em>
          </div>
          <div class="movement-item">
            <div class="movement-icon gold">=</div>
            <div class="movement-copy">
              <strong>Entradas menos vendas</strong>
              <span>${numberFormat(balance)}</span>
            </div>
            <em>unidades</em>
          </div>
        `;
      }

      const alertList = document.getElementById('alert-list');
      if (alertList) {
        const limitedAlerts = alerts.slice(0, 4);
        alertList.innerHTML = limitedAlerts.length
          ? limitedAlerts.map((alert) => `
              <div class="alert-line">
                <span class="alert-bullet"></span>
                <div>
                  <strong>${escapeHtml(ALERT_LABELS[alert.tipo] || alert.tipo || 'Alerta')}</strong>
                  <p>${escapeHtml(alert.mensagem || 'Produto com atenção.')}</p>
                </div>
              </div>
            `).join('')
          : '<div class="alert-line"><span class="alert-bullet"></span><div><strong>Sem alertas</strong><p>Nenhuma ocorrência pendente.</p></div></div>';
      }
    } catch (error) {
      const statCards = document.querySelectorAll('.stat-card');
      statCards.forEach((card) => {
        const value = card.querySelector('.stat-value');
        if (value) value.textContent = '—';
      });

      const salesChart = document.getElementById('sales-chart');
      if (salesChart) salesChart.innerHTML = '<div class="empty-state-row">Não foi possível carregar os dados.</div>';

      const expirationTableBody = document.getElementById('expiration-table-body');
      if (expirationTableBody) expirationTableBody.innerHTML = '<tr><td colspan="4" class="empty-state-row">Não foi possível carregar a validade.</td></tr>';
    }
  };

  renderReports();
});
