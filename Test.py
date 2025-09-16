import streamlit as st
import pandas as pd
import numpy as np
import io
import datetime
import locale
import yfinance as yf

# --- Configuração da Página ---
st.set_page_config(
    page_title="Analisador de Backtest",
    page_icon="✨",
    layout="wide"
)

# Tenta configurar o locale para Português
try:
    locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
except locale.Error:
    st.warning("Locale 'pt_BR.UTF-8' não encontrado. Os dias da semana podem aparecer em inglês.")

# --- Bloco de Funções ---

def carregar_dados(arquivo_enviado):
    if arquivo_enviado is None: return None
    try:
        if arquivo_enviado.name.endswith('.csv'):
            dados = pd.read_csv(arquivo_enviado, sep=';', encoding='latin1', header=0, decimal=',')
            st.success("Arquivo CSV lido com sucesso.")
            return dados
        elif arquivo_enviado.name.endswith('.xlsx'):
            dados = pd.read_excel(arquivo_enviado, engine='openpyxl')
            st.success("Arquivo XLSX lido com sucesso.")
            return dados
    except Exception as e:
        st.error(f"Falha ao ler o arquivo. Verifique o formato e a codificação. Erro: {e}")
        return None

def processar_dados(dados, modo_analise):
    dados_processados = dados.copy()
    try:
        if modo_analise == "Análise Intraday":
            dados_processados['Data'] = pd.to_datetime(dados_processados['Data'], dayfirst=True, errors='coerce')
            dados_processados.dropna(subset=['Data'], inplace=True)
            if 'Hora' not in dados_processados.columns:
                st.error("Para a Análise Intraday com arquivo, a coluna 'Hora' é necessária.")
                return None
            dados_processados['Timestamp'] = pd.to_datetime(dados_processados['Data'].dt.strftime('%Y-%m-%d') + ' ' + dados_processados['Hora'].astype(str))
            dados_processados = dados_processados.set_index('Timestamp').sort_index()
        elif modo_analise == "Análise Day Trade":
            if isinstance(dados.index, pd.DatetimeIndex):
                dados_processados = dados
            else:
                dados_processados['Data'] = pd.to_datetime(dados_processados['Data'], dayfirst=True, errors='coerce')
                dados_processados.dropna(subset=['Data'], inplace=True)
                dados_processados['Data'] = dados_processados['Data'].dt.normalize()
                dados_processados.drop_duplicates(subset=['Data'], keep='first', inplace=True)
                dados_processados = dados_processados.set_index('Data')
    except Exception as e:
        st.error(f"Erro ao processar a coluna 'Data'. Verifique o formato. Erro: {e}")
        return None
    return dados_processados

@st.cache_data
def buscar_dados_intraday_online(ticker, data_inicio, data_fim, tipo_ativo):
    if (data_fim - data_inicio).days > 60:
        st.error("Período muito longo! Para dados intraday de 15 minutos, o Yahoo Finance permite buscar no máximo 60 dias.")
        return None
    ticker_formatado = ticker.upper()
    if tipo_ativo == "Ação (Brasil)":
        if not ticker_formatado.endswith('.SA'): ticker_formatado = f"{ticker_formatado}.SA"
    elif tipo_ativo == "Forex":
        ticker_formatado = f"{ticker_formatado}=X"
    try:
        ativo = yf.Ticker(ticker_formatado)
        dados = ativo.history(start=data_inicio, end=data_fim, interval="15m", auto_adjust=False)
        if dados.empty:
            st.error(f"Nenhum dado intraday encontrado para '{ticker_formatado}'. O ativo pode não ter liquidez ou o período é muito antigo.")
            return None
        dados.index = dados.index.tz_convert('America/Sao_Paulo').tz_localize(None)
        dados.rename(columns={'Open': 'Abertura', 'High': 'Máxima', 'Low': 'Mínima', 'Close': 'Fechamento'}, inplace=True)
        return dados
    except Exception as e:
        st.error(f"Falha ao buscar dados online. Erro: {e}")
        return None

def aplicar_gatilho_e_criar_resumo(df, variacao_teste, hora_final):
    df_original = df.copy()
    fechamentos_diarios = df_original.resample('D')['Fechamento'].last()
    fechamento_anterior = fechamentos_diarios.shift(1).dropna()
    dias_com_gatilho = []
    for dia, fech_anterior in fechamento_anterior.items():
        preco_gatilho = fech_anterior * (1 + variacao_teste / 100)
        dados_do_dia = df_original[df_original.index.date == dia.date()]
        candle_gatilho = None
        if variacao_teste > 0:
            primeiro_candle_acima = dados_do_dia[dados_do_dia['Máxima'] >= preco_gatilho]
            if not primeiro_candle_acima.empty: candle_gatilho = primeiro_candle_acima.iloc[0]
        else:
            primeiro_candle_abaixo = dados_do_dia[dados_do_dia['Mínima'] <= preco_gatilho]
            if not primeiro_candle_abaixo.empty: candle_gatilho = primeiro_candle_abaixo.iloc[0]
        if candle_gatilho is not None:
            hora_entrada, hora_saida = candle_gatilho.name, datetime.datetime.combine(dia.date(), hora_final)
            if hora_saida > hora_entrada:
                candle_saida_lookup = df_original.asof(hora_saida)
                dados_operacao = dados_do_dia.loc[hora_entrada:hora_saida]
                if dados_operacao.empty: continue
                dias_com_gatilho.append({'Timestamp': dia, 'Hora Abertura': candle_gatilho.name.time(),'Abertura': preco_gatilho, 'Hora Máxima': dados_operacao['Máxima'].idxmax().time(),'Maxima': dados_operacao['Máxima'].max(), 'Hora Mínima': dados_operacao['Mínima'].idxmin().time(),'Minima': dados_operacao['Mínima'].min(), 'Hora Fechamento': candle_saida_lookup.name.time(),'Fechamento': candle_saida_lookup['Fechamento']})
    if not dias_com_gatilho: return None
    return pd.DataFrame(dias_com_gatilho).set_index('Timestamp')

def criar_resumo_por_horario_fixo(df, hora_inicial, hora_final):
    operacoes_diarias = []
    for dia, dados_do_dia in df.groupby(df.index.date):
        if hora_inicial >= hora_final: continue
        dados_operacao = dados_do_dia.between_time(hora_inicial, hora_final)
        if dados_operacao.empty: continue
        candle_entrada = dados_operacao.iloc[0]
        candle_saida = dados_operacao.iloc[-1]
        operacoes_diarias.append({'Timestamp': pd.to_datetime(dia), 'Hora Abertura': candle_entrada.name.time(), 'Abertura': candle_entrada['Abertura'], 'Hora Máxima': dados_operacao['Máxima'].idxmax().time(), 'Maxima': dados_operacao['Máxima'].max(), 'Hora Mínima': dados_operacao['Mínima'].idxmin().time(), 'Minima': dados_operacao['Mínima'].min(), 'Hora Fechamento': candle_saida.name.time(), 'Fechamento': candle_saida['Fechamento']})
    if not operacoes_diarias: return None
    return pd.DataFrame(operacoes_diarias).set_index('Timestamp')

@st.cache_data
def buscar_dados_online_daytrade(ticker, data_inicio, data_fim, tipo_ativo):
    ticker_formatado = ticker.upper()
    if tipo_ativo == "Ação (Brasil)":
        if not ticker_formatado.endswith('.SA'): ticker_formatado = f"{ticker_formatado}.SA"
    elif tipo_ativo == "Forex":
        ticker_formatado = f"{ticker_formatado}=X"
    try:
        ativo = yf.Ticker(ticker_formatado)
        dados = ativo.history(start=data_inicio, end=data_fim, auto_adjust=False)
        if dados.empty:
            st.error(f"Nenhum dado encontrado para o ticker '{ticker_formatado}'. Verifique o código do ativo, o tipo ou o período.")
            return None
        dados.index = dados.index.tz_localize(None)
        return dados
    except Exception as e:
        st.error(f"Falha ao buscar dados online. Erro: {e}")
        return None

def preparar_dados_day_trade(df):
    df_prep = df.copy()
    df_prep.rename(columns={'Open': 'Abertura', 'High': 'Máxima', 'Low': 'Mínima', 'Close': 'Fechamento', 'Volume': 'Volume'}, inplace=True)
    df_prep['Fechamento_Anterior'] = df_prep['Fechamento'].shift(1)
    df_prep.dropna(inplace=True)
    df_prep['% Abertura'] = (df_prep['Abertura'] / df_prep['Fechamento_Anterior'] - 1)
    df_prep['% Máxima'] = (df_prep['Máxima'] / df_prep['Fechamento_Anterior'] - 1)
    df_prep['% Mínima'] = (df_prep['Mínima'] / df_prep['Fechamento_Anterior'] - 1)
    df_prep['% Fechamento'] = (df_prep['Fechamento'] / df_prep['Fechamento_Anterior'] - 1)
    df_prep.index.name = 'Data'
    colunas_necessarias = ['Abertura', 'Máxima', 'Mínima', 'Fechamento', 'Volume', '% Abertura', '% Máxima', '% Mínima', '% Fechamento']
    return df_prep[colunas_necessarias]

def simular_day_trade_com_percentagens(df, variacao_teste, tipo_operacao):
    operacoes = []
    df_copy = df.copy()
    variacao_teste_decimal = variacao_teste / 100.0
    for index, dia in df_copy.iterrows():
        gatilho_atingido = False
        ponto_zero_decimal = 0.0
        abertura_pct_decimal = dia['% Abertura']
        maxima_pct_decimal = dia['% Máxima']
        minima_pct_decimal = dia['% Mínima']
        fechamento_pct_decimal = dia['% Fechamento']
        abertura_acionou = False
        if variacao_teste_decimal > 0 and abertura_pct_decimal >= variacao_teste_decimal: abertura_acionou = True
        elif variacao_teste_decimal < 0 and abertura_pct_decimal <= variacao_teste_decimal: abertura_acionou = True
        if abertura_acionou:
            gatilho_atingido = True
            ponto_zero_decimal = abertura_pct_decimal
        else:
            if variacao_teste_decimal > 0 and maxima_pct_decimal >= variacao_teste_decimal:
                gatilho_atingido = True; ponto_zero_decimal = variacao_teste_decimal
            elif variacao_teste_decimal < 0 and minima_pct_decimal <= variacao_teste_decimal:
                gatilho_atingido = True; ponto_zero_decimal = variacao_teste_decimal
        if gatilho_atingido:
            resultado_decimal = 0.0
            if tipo_operacao == 'Compra': resultado_decimal = fechamento_pct_decimal - ponto_zero_decimal
            else: resultado_decimal = ponto_zero_decimal - fechamento_pct_decimal
            operacoes.append({'Data': index, 'Abertura': dia['Abertura'], 'Maxima': dia['Máxima'], 'Minima': dia['Mínima'], 'Fechamento': dia['Fechamento'], '% Abertura': abertura_pct_decimal, '% Máxima': maxima_pct_decimal, '% Mínima': minima_pct_decimal, '% Fechamento': fechamento_pct_decimal, 'Resultado %': resultado_decimal, 'Preco_Entrada_Pct': ponto_zero_decimal})
    if not operacoes: return None
    return pd.DataFrame(operacoes).set_index('Data')

def calcular_metricas_de_resumo(resumo_periodo, tipo_operacao):
    if resumo_periodo is None or len(resumo_periodo) < 1: return None
    total_trades = len(resumo_periodo)
    if 'Resultado %' not in resumo_periodo.columns:
        if tipo_operacao == 'Compra':
            resumo_periodo['Resultado %'] = (resumo_periodo['Fechamento'] - resumo_periodo['Abertura']) / resumo_periodo['Abertura']
        else:
            resumo_periodo['Resultado %'] = (resumo_periodo['Abertura'] - resumo_periodo['Fechamento']) / resumo_periodo['Abertura']
    resultado_op_decimal = resumo_periodo['Resultado %']
    acertos = (resultado_op_decimal > 0).sum()
    erros = total_trades - acertos
    taxa_acerto = (acertos / total_trades) * 100 if total_trades > 0 else 0
    taxa_erro = (erros / total_trades) * 100 if total_trades > 0 else 0
    melhor_momento, pior_momento = 0.0, 0.0
    if 'Preco_Entrada_Pct' in resumo_periodo.columns:
        if tipo_operacao == 'Compra':
            melhor_momento = (resumo_periodo['% Máxima'] - resumo_periodo['Preco_Entrada_Pct']).max() * 100
            pior_momento = (resumo_periodo['% Mínima'] - resumo_periodo['Preco_Entrada_Pct']).min() * 100
        else:
            melhor_momento = (resumo_periodo['Preco_Entrada_Pct'] - resumo_periodo['% Mínima']).max() * 100
            pior_momento = (resumo_periodo['Preco_Entrada_Pct'] - resumo_periodo['% Máxima']).min() * 100
    else:
        if tipo_operacao == 'Compra':
            melhor_momento = ((resumo_periodo['Maxima'] - resumo_periodo['Abertura']) / resumo_periodo['Abertura']).max() * 100
            pior_momento = ((resumo_periodo['Minima'] - resumo_periodo['Abertura']) / resumo_periodo['Abertura']).min() * 100
        else:
            melhor_momento = ((resumo_periodo['Abertura'] - resumo_periodo['Minima']) / resumo_periodo['Abertura']).max() * 100
            pior_momento = ((resumo_periodo['Abertura'] - resumo_periodo['Maxima']) / resumo_periodo['Abertura']).min() * 100
    metricas = {"Total de Trades": total_trades, "Nº de Acertos": acertos, "Nº de Erros": erros, "Taxa de Acerto (%)": taxa_acerto, "Taxa de Erro (%)": taxa_erro, "Resultado Final Acumulado (%)": resultado_op_decimal.sum() * 100, "Ganho Médio (% por Trade)": resultado_op_decimal.mean() * 100, "Ganho Máximo (1 Trade %)": resultado_op_decimal.max() * 100, "Perda Máxima (1 Trade %)": resultado_op_decimal.min() * 100, "Melhor Momento (Excursão Favorável %)": melhor_momento, "Pior Momento (Excursão Adversa %)": pior_momento}
    return metricas

def calcular_metricas_recentes(resumo_periodo):
    if resumo_periodo is None or len(resumo_periodo) < 5: return None
    resultados = []
    periodos = [5, 10, 15, 20, 25]
    for p in periodos:
        if len(resumo_periodo) >= p:
            ultimos_trades = resumo_periodo.tail(p)
            resultado_op_decimal = ultimos_trades['Resultado %']
            ganho_medio = resultado_op_decimal.mean() * 100
            acertos = (resultado_op_decimal > 0).sum()
            taxa_acerto = (acertos / p) * 100
            resultados.append({"Período (Últimos Trades)": f"{p}", "Ganho Médio (%)": ganho_medio, "% Acertos": taxa_acerto})
    if not resultados: return None
    return pd.DataFrame(resultados).set_index("Período (Últimos Trades)")

def calcular_metricas_recentes_por_dia_semana(resumo_periodo):
    if resumo_periodo is None or resumo_periodo.empty: return None
    df = resumo_periodo.copy()
    mapa_dias = {0: 'Segunda-feira', 1: 'Terça-feira', 2: 'Quarta-feira', 3: 'Quinta-feira', 4: 'Sexta-feira'}
    df['Dia da Semana'] = df.index.dayofweek.map(mapa_dias)
    df.dropna(subset=['Dia da Semana'], inplace=True)
    resultados_lista = []
    periodos = [5, 10, 15, 20, 25]
    dias_semana_ordenados = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira']
    for dia in dias_semana_ordenados:
        trades_do_dia = df[df['Dia da Semana'] == dia]
        for p in periodos:
            if len(trades_do_dia) >= p:
                ultimos_trades = trades_do_dia.tail(p)
                resultado_op_decimal = ultimos_trades['Resultado %']
                ganho_medio = resultado_op_decimal.mean() * 100
                acertos = (resultado_op_decimal > 0).sum()
                taxa_acerto = (acertos / p) * 100
                resultados_lista.append({"Dia da Semana": dia, "Período (Trades)": p, "Ganho Médio (%)": ganho_medio, "% Acertos": taxa_acerto})
    if not resultados_lista: return None
    df_resultados = pd.DataFrame(resultados_lista)
    try:
        pivot = df_resultados.pivot_table(index="Período (Trades)", columns="Dia da Semana", values=["Ganho Médio (%)", "% Acertos"])
        dias_presentes = df_resultados['Dia da Semana'].unique()
        dias_ordenados_presentes = [dia for dia in dias_semana_ordenados if dia in dias_presentes]
        pivot = pivot.swaplevel(0, 1, axis=1)[dias_ordenados_presentes]
        pivot.index = pd.to_numeric(pivot.index)
        pivot = pivot.sort_index()
        pivot.index = pivot.index.astype(str)
        return pivot
    except Exception:
        return None

def criar_tabela_dia_semana(resumo_periodo, tipo_operacao):
    if resumo_periodo is None or resumo_periodo.empty: return None
    df = resumo_periodo.copy()
    mapa_dias = {0: 'Segunda-feira', 1: 'Terça-feira', 2: 'Quarta-feira', 3: 'Quinta-feira', 4: 'Sexta-feira', 5: 'Sábado', 6: 'Domingo'}
    df['Dia da Semana Num'] = df.index.dayofweek
    df['Resultado Op'] = df['Resultado %'] * 100
    df['Acerto'] = df['Resultado Op'] > 0
    tabela = df.groupby('Dia da Semana Num').agg(Total=('Resultado Op', 'count'), Acertos=('Acerto', 'sum'), Lucro_Medio_Pct=('Resultado Op', 'mean'))
    tabela.index = tabela.index.map(mapa_dias); tabela.index.name = "Dia da Semana"
    tabela['Erros'] = tabela['Total'] - tabela['Acertos']
    tabela['% Acertos'] = (tabela['Acertos'] / tabela['Total']) * 100
    tabela['% Erros'] = (tabela['Erros'] / tabela['Total']) * 100
    if not tabela.empty:
        somas = tabela[['Acertos', 'Erros', 'Total']].sum()
        medias = tabela[['% Acertos', '% Erros', 'Lucro_Medio_Pct']].mean()
        total_row = pd.concat([somas, medias]); total_row.name = 'Total / Média'
        tabela = pd.concat([tabela, pd.DataFrame(total_row).T])
    ordem_dias = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    tabela_ordenada = tabela.reindex(index=ordem_dias).dropna(how='all')
    if 'Total / Média' in tabela.index: tabela_ordenada = pd.concat([tabela_ordenada, tabela.loc[['Total / Média']]])
    tabela_ordenada.rename(columns={'Acertos': 'Nº de Acertos', 'Erros': 'Nº de Erros', 'Total': 'Total de Eventos', 'Lucro_Medio_Pct': '% Lucro Médio'}, inplace=True)
    return tabela_ordenada[['Nº de Acertos', 'Nº de Erros', 'Total de Eventos', '% Acertos', '% Erros', '% Lucro Médio']]

# --- Interface Principal ---
st.title("📈 Analisador de Backtest")

if 'day_trade_data' not in st.session_state: st.session_state.day_trade_data = None
if 'intraday_data' not in st.session_state: st.session_state.intraday_data = None

modo_analise = st.selectbox("Selecione o Modo de Análise", ("Análise Intraday", "Análise Day Trade"))

df_processado = None

if modo_analise == "Análise Intraday":
    st.session_state.day_trade_data = None
    st.write("Escolha a fonte dos dados para a análise Intraday (gráfico de 15 min).")
    fonte_dados_intraday = st.radio("Fonte dos Dados Intraday", ("Fazer Upload de Arquivo", "Buscar Online (Yahoo Finance)"), horizontal=True, key="fonte_intraday")
    if fonte_dados_intraday == "Fazer Upload de Arquivo":
        arquivo_csv = st.file_uploader("Selecione o arquivo CSV ou XLSX", type=["csv", "xlsx"], key="intraday_uploader")
        if arquivo_csv:
            df_bruto = carregar_dados(arquivo_csv)
            if df_bruto is not None:
                st.session_state.intraday_data = processar_dados(df_bruto, modo_analise)
    elif fonte_dados_intraday == "Buscar Online (Yahoo Finance)":
        st.info("A busca online para dados intraday está limitada aos últimos 60 dias.")
        col1, col2 = st.columns([1, 2])
        with col1:
            tipo_ativo_intraday = st.selectbox("Tipo de Ativo", ["Ação (Brasil)", "Forex", "Criptomoeda"], key="tipo_ativo_intraday")
        with col2:
            placeholder_text = "Ex: PETR4"
            if tipo_ativo_intraday == "Forex": placeholder_text = "Ex: EURUSD"
            elif tipo_ativo_intraday == "Criptomoeda": placeholder_text = "Ex: BTC-USD"
            ticker_intraday = st.text_input("Código do Ativo", placeholder=placeholder_text, key="ticker_intraday")
        col_data1, col_data2, col_btn = st.columns([2, 2, 1])
        with col_data1:
            hoje = datetime.date.today()
            data_inicio_intraday = st.date_input("Data de Início", hoje - datetime.timedelta(days=59), key="data_inicio_intraday")
        with col_data2:
            data_fim_intraday = st.date_input("Data de Fim", hoje, key="data_fim_intraday")
        with col_btn:
            st.write("")
            if st.button("Buscar Dados Intraday", use_container_width=True):
                if ticker_intraday and data_inicio_intraday and data_fim_intraday:
                    with st.spinner(f"Buscando dados para {ticker_intraday}..."):
                        dados_online = buscar_dados_intraday_online(ticker_intraday, data_inicio_intraday, data_fim_intraday, tipo_ativo_intraday)
                        if dados_online is not None:
                            st.session_state.intraday_data = dados_online
                            st.success(f"Dados de {ticker_intraday} carregados!")
    if st.session_state.intraday_data is not None:
        df_processado = st.session_state.intraday_data
        if st.sidebar.button("Limpar Dados Intraday"):
            st.session_state.intraday_data = None
            st.rerun()

elif modo_analise == "Análise Day Trade":
    st.session_state.intraday_data = None
    st.write("Busque por um ativo para iniciar a análise Day Trade.")
    col1, col2 = st.columns([1, 2])
    with col1:
        tipo_ativo = st.selectbox("Tipo de Ativo", ["Ação (Brasil)", "Forex", "Criptomoeda"])
    with col2:
        placeholder_text = "Ex: PETR4"
        if tipo_ativo == "Forex": placeholder_text = "Ex: EURUSD, EURBRL"
        elif tipo_ativo == "Criptomoeda": placeholder_text = "Ex: BTC-USD, ETH-BRL"
        ticker = st.text_input("Código do Ativo", placeholder=placeholder_text)
    col_data1, col_data2, col_btn = st.columns([2, 2, 1])
    with col_data1:
        hoje = datetime.date.today()
        data_inicio = st.date_input("Data de Início", hoje - datetime.timedelta(days=365*2))
    with col_data2:
        data_fim = st.date_input("Data de Fim", hoje)
    with col_btn:
        st.write("")
        if st.button("Buscar Dados", use_container_width=True):
            if ticker and data_inicio and data_fim:
                if data_inicio >= data_fim:
                    st.error("A data de início deve ser anterior à data de fim.")
                else:
                    with st.spinner(f"Buscando dados para {ticker}..."):
                        dados_brutos_online = buscar_dados_online_daytrade(ticker, data_inicio, data_fim, tipo_ativo)
                        if dados_brutos_online is not None:
                            st.session_state.day_trade_data = preparar_dados_day_trade(dados_brutos_online)
                            st.success(f"Dados de {ticker} carregados!")
            else:
                st.warning("Por favor, preencha o código do ativo.")
    if st.session_state.day_trade_data is not None:
        df_processado = st.session_state.day_trade_data
        if st.sidebar.button("Limpar Dados e Nova Busca"):
            st.session_state.day_trade_data = None
            st.rerun()

if df_processado is not None:
    st.sidebar.header(f"⚙️ Parâmetros - {modo_analise}")
    resumo_base = None
    tipo_operacao = 'Compra'
    dias_selecionados_num = []

    if modo_analise == "Análise Intraday":
        st.sidebar.subheader("Modo de Análise")
        ativar_gatilho = st.sidebar.checkbox("Ativar Gatilho por Variação")
        variacao_teste = 0.0
        if ativar_gatilho:
            gatilho_negativo = st.sidebar.checkbox("Tornar Variação Negativa")
            valor_input = st.sidebar.number_input("Variação de Teste para Entrada (%)", min_value=0.00, max_value=100.00, value=0.50, step=0.01, format="%.2f")
            variacao_teste = -valor_input if gatilho_negativo else valor_input
        tipo_operacao = st.sidebar.radio("Tipo de Operação", ('Compra', 'Venda'), horizontal=True)
        st.sidebar.subheader("Janela de Tempo")
        if not ativar_gatilho:
            hora_inicial = st.sidebar.time_input("Hora Inicial", value=df_processado.index.time.min() if len(df_processado.index.time) > 0 else datetime.time(9, 0))
            tempo_grafico = "Diário (por Horário Fixo)"
        else:
            tempo_grafico = "Diário (por Gatilho)"
        hora_final = st.sidebar.time_input("Hora Final", value=df_processado.index.time.max() if len(df_processado.index.time) > 0 else datetime.time(18, 0))
        st.sidebar.markdown("---")
        st.sidebar.subheader("Filtros da Tabela Semanal")
        dias_semana_map = {"Segunda-feira": 0, "Terça-feira": 1, "Quarta-feira": 2, "Quinta-feira": 3, "Sexta-feira": 4, "Sábado": 5, "Domingo": 6}
        dias_selecionados_num = [num for dia, num in dias_semana_map.items() if st.sidebar.checkbox(dia, value=True, key=f"day_intraday_{num}")]
        
        if ativar_gatilho:
            resumo_base = aplicar_gatilho_e_criar_resumo(df_processado, variacao_teste, hora_final)
        else:
            resumo_base = criar_resumo_por_horario_fixo(df_processado, hora_inicial, hora_final)
        st.header(f"📊 Painel de Resultados - {tempo_grafico}")
        
    elif modo_analise == "Análise Day Trade":
        st.sidebar.subheader("Estratégia de Gatilho")
        valor_input_dt = st.sidebar.number_input("Variação Teste (%)", min_value=-100.00, max_value=100.00, value=2.0, step=0.1, format="%.2f")
        tipo_operacao = st.sidebar.radio("Tipo de Operação", ('Compra', 'Venda'), horizontal=True)
        st.sidebar.markdown("---")
        st.sidebar.subheader("Filtros da Tabela Semanal")
        dias_semana_map = {"Segunda-feira": 0, "Terça-feira": 1, "Quarta-feira": 2, "Quinta-feira": 3, "Sexta-feira": 4}
        dias_selecionados_num = [num for dia, num in dias_semana_map.items() if st.sidebar.checkbox(dia, value=True, key=f"day_daytrade_{num}")]
        resumo_base = simular_day_trade_com_percentagens(df_processado, valor_input_dt, tipo_operacao)
        st.header("📊 Painel de Resultados")

    resultados_gerais = calcular_metricas_de_resumo(resumo_base, tipo_operacao)
    
    if resultados_gerais:
        st.subheader("📄 Métricas de Desempenho")
        r = resultados_gerais
        st.markdown("<h6>Visão Geral da Estratégia</h6>", unsafe_allow_html=True)
        cols1 = st.columns(6)
        cols1[0].metric("Trades", f"{r.get('Total de Trades', 0):.0f}")
        cols1[1].metric("Acertos", f"{r.get('Nº de Acertos', 0):.0f}")
        cols1[2].metric("% Acertos", f"{r.get('Taxa de Acerto (%)', 0):.2f}%")
        cols1[3].metric("Erros", f"{r.get('Nº de Erros', 0):.0f}")
        cols1[4].metric("% Erros", f"{r.get('Taxa de Erro (%)', 0):.2f}%")
        cols1[5].metric("Resultado Final", f"{r.get('Resultado Final Acumulado (%)', 0):.2f}%")
        st.markdown("---")
        st.markdown("<h6>Análise de Risco e Retorno</h6>", unsafe_allow_html=True)
        cols2 = st.columns(5)
        cols2[0].metric("Ganho Médio", f"{r.get('Ganho Médio (% por Trade)', 0):.2f}%")
        cols2[1].metric("Ganho Máximo", f"{r.get('Ganho Máximo (1 Trade %)', 0):.2f}%")
        cols2[2].metric("Perda Máxima", f"{r.get('Perda Máxima (1 Trade %)', 0):.2f}%")
        cols2[3].metric("Melhor Momento", f"{r.get('Melhor Momento (Excursão Favorável %)', 0):.2f}%")
        cols2[4].metric("Pior Momento", f"{r.get('Pior Momento (Excursão Adversa %)', 0):.2f}%")
    else:
        st.warning("Nenhum trade foi gerado para os parâmetros definidos.")

    st.markdown("---")
    df_recentes = calcular_metricas_recentes(resumo_base)
    if df_recentes is not None:
        st.subheader("📈 Performance Recente (Geral)")
        st.dataframe(df_recentes.style.format("{:.2f}%"))
    
    df_recentes_dia = calcular_metricas_recentes_por_dia_semana(resumo_base)
    if df_recentes_dia is not None:
        st.subheader("📊 Performance Recente por Dia da Semana")
        st.dataframe(df_recentes_dia.style.format("{:.2f}%").background_gradient(cmap='RdYlGn', axis=None, subset=[(d, '% Acertos') for d in df_recentes_dia.columns.get_level_values(0).unique() if (d, '% Acertos') in df_recentes_dia.columns]))
    else:
        st.info("A tabela de 'Performance Recente por Dia da Semana' não foi gerada pois não há dados suficientes (mínimo de 5 trades para pelo menos um dia da semana). Tente um período de análise mais longo.")
    
    with st.expander("Visualizar Trades Contabilizados"):
        if resumo_base is not None and not resumo_base.empty:
            tabela_trades = resumo_base.copy()
            # Formatação do índice e colunas percentuais
            if isinstance(resumo_base.index, pd.DatetimeIndex):
                tabela_trades.index = tabela_trades.index.strftime('%d/%m/%Y')
            colunas_pct = ['% Abertura', '% Máxima', '% Mínima', '% Fechamento', 'Resultado %']
            for col in colunas_pct:
                if col in tabela_trades.columns:
                    tabela_trades[col] = tabela_trades[col] * 100
            st.dataframe(tabela_trades.style.format(formatter={'Abertura': '{:,.2f}', 'Maxima': '{:,.2f}', 'Minima': '{:,.2f}', 'Fechamento': '{:,.2f}', '% Abertura': '{:,.2f}', '% Máxima': '{:,.2f}', '% Mínima': '{:,.2f}', '% Fechamento': '{:,.2f}', 'Resultado %': '{:,.2f}'}, decimal=',', thousands='.'))
    
    st.header("🗓️ Análise por Dia da Semana")
    resumo_filtrado_semana = resumo_base
    if resumo_base is not None and dias_selecionados_num:
        resumo_filtrado_semana = resumo_base[resumo_base.index.dayofweek.isin(dias_selecionados_num)]
    
    tabela_semanal = criar_tabela_dia_semana(resumo_filtrado_semana, tipo_operacao)
    if tabela_semanal is not None:
        st.dataframe(tabela_semanal.style.format({'Nº de Acertos': '{:.0f}', 'Nº de Erros': '{:.0f}', 'Total de Eventos': '{:.0f}', '% Acertos': '{:,.2f}%', '% Erros': '{:,.2f}%', '% Lucro Médio': '{:,.2f}%'}, decimal=',', thousands='.'))

    with st.expander("Visualizar Tabela de Dados Processados"):
        st.dataframe(df_processado.style.format(decimal=',', thousands='.'))