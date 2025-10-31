from flask import Flask, render_template, request, redirect, url_for, flash, session
import json
import pandas as pd
from decimal import Decimal, ROUND_HALF_UP
import os
import requests
from datetime import datetime, timedelta
import re

app = Flask(__name__)
app.secret_key = "Helen_*2025"
app.permanent_session_lifetime = timedelta(hours=24)

# 🔧 Caminho do arquivo local das alíquotas
ALIQUOTAS_FILE = "uploads/Aliquotas_internas.json"

# 🔧 Arquivo local da base de produtos
PRODUCT_BASE_FILE = "product_base.json"

# 🔧 Usuários autorizados
USERS = {
    "admin": "admin123",
    "helen": "helen2025",
    "user": "user123"
}

# 🔧 Lista padrão de UFs
UFS_PADRAO = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO"
]

def login_required(f):
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('⚠️ Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

def atualizar_aliquotas():
    try:
        if not os.path.exists(ALIQUOTAS_FILE):
            print(f"[{datetime.now()}] ⚠️ Arquivo de alíquotas não encontrado, usando lista padrão de UFs.")
            return {uf: 18.0 for uf in UFS_PADRAO}  # Default 18%
        
        with open(ALIQUOTAS_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
        
        if not dados:
            dados = {uf: 18.0 for uf in UFS_PADRAO}
        
        with open(ALIQUOTAS_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        
        print(f"[{datetime.now()}] ✅ Alíquotas carregadas/atualizadas com sucesso.")
        return dados
    except Exception as e:
        print(f"Erro ao atualizar alíquotas: {e}")
        return {uf: 18.0 for uf in UFS_PADRAO}

# Carrega alíquotas
STATE_RATES = atualizar_aliquotas()

# Base de produtos carregada de planilha
PRODUCT_DB = {}

if os.path.exists(PRODUCT_BASE_FILE):
    with open(PRODUCT_BASE_FILE, "r", encoding="utf-8") as f:
        PRODUCT_DB = json.load(f)

if not os.path.exists("uploads"):
    os.makedirs("uploads")

@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/index", methods=["GET", "POST"])
def login():
    if 'logged_in' in session:
        return redirect(url_for('site'))
    
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        
        if username in USERS and USERS[username] == password:
            session.permanent = True
            session['logged_in'] = True
            session['username'] = username
            session['login_time'] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            
            flash(f"✅ Login realizado com sucesso! Bem-vindo(a), {username}.", "success")
            return redirect(url_for('site'))
        else:
            flash("❌ Usuário ou senha incorretos. Tente novamente.", "danger")
    
    return render_template("index.html")

@app.route("/logout")
@login_required
def logout():
    username = session.get('username', 'Usuário')
    session.clear()
    flash(f"👋 Logout realizado com sucesso. Até logo, {username}!", "info")
    return redirect(url_for('login'))

@app.route("/site")
@login_required
def site():
    ufs = sorted(STATE_RATES.keys())
    return render_template(
        "site.html",
        ufs=ufs,
        produtos=PRODUCT_DB,
        produtos_extraidos=None,
        state_rates=STATE_RATES,
        username=session.get('username', 'Usuário')
    )

def load_product_base(file_path):
    global PRODUCT_DB
    df = pd.read_excel(file_path) if file_path.endswith(".xlsx") else pd.read_csv(file_path)
    if not {"Codigo", "Descricao", "Origem"}.issubset(df.columns):
        raise ValueError("Planilha deve conter as colunas Codigo, Descricao e Origem")
    PRODUCT_DB = {
        str(row["Codigo"]).strip(): {
            "descricao": str(row["Descricao"]).strip(),
            "origem": str(row["Origem"]).strip().lower()
        }
        for _, row in df.iterrows()
    }
    with open(PRODUCT_BASE_FILE, "w", encoding="utf-8") as f:
        json.dump(PRODUCT_DB, f, indent=2, ensure_ascii=False)

def calc_difal(valor_total_produtos, valor_frete, valor_seguro, valor_outros, valor_desconto, origem, aliquota_interna_pct):
    """
    Calcula DIFAL conforme a origem do produto:
    - IMPORTADO: DIFAL = 4%
    - NACIONAL: DIFAL = Alíquota Interna - 12%
    
    Agora inclui frete, seguro, outros e desconto no cálculo da base de cálculo
    """
    # Converte para Decimal
    aliquota_interna = Decimal(str(aliquota_interna_pct))
    
    if origem == "importado":
        # Para importado: DIFAL fixo em 4%
        difal_pct = Decimal("4.00")
    else:
        # Para nacional: DIFAL = Alíquota Interna - 12%
        difal_pct = aliquota_interna - Decimal("12.00")
        if difal_pct < 0:
            difal_pct = Decimal("0.00")
    
    # Calcula a base de cálculo incluindo frete, seguro, outros e SUBTRAINDO desconto
    base_calculo = (valor_total_produtos + valor_frete + valor_seguro + valor_outros - valor_desconto)
    
    # Garante que a base de cálculo não seja negativa
    if base_calculo < 0:
        base_calculo = Decimal("0.00")
    
    # Calcula o DIFAL: base_calculo × (%DIFAL / 100)
    valor_difal = (base_calculo * (difal_pct / Decimal("100.00"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    return difal_pct, valor_difal, base_calculo

def extract_text_from_pdf(file_path):
    text = ""
    
    try:
        import PyPDF2
        with open(file_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        if text.strip():
            print("Texto extraído com PyPDF2")
            return text
    except ImportError:
        print("PyPDF2 não instalado")
    except Exception as e:
        print(f"Erro com PyPDF2: {e}")
    
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        if text.strip():
            print("Texto extraído com pdfplumber")
            return text
    except ImportError:
        print("pdfplumber não instalado")
    except Exception as e:
        print(f"Erro com pdfplumber: {e}")
    
    return text

def parse_extracted_text(text):
    items = []
    lines = text.split('\n')
    
    print("=== DEBUG INÍCIO DA EXTRAÇÃO ===")
    
    in_items_section = False
    items_section_lines = []
    
    # Variáveis para informações gerais
    valor_frete = 0.0
    valor_desconto = 0.0
    in_general_info = False
    
    for i, line in enumerate(lines):
        # Seção de ITENS DO DOCUMENTO
        if 'ITENS DO DOCUMENTO' in line or ('Código' in line and 'Descrição' in line):
            in_items_section = True
            in_general_info = False
            print("✅ Entrou na seção de itens")
            continue
        
        # Seção de INFORMAÇÕES GERAIS
        if 'INFORMAÇÕES GERAIS' in line:
            in_items_section = False
            in_general_info = True
            print("✅ Entrou na seção de informações gerais")
            continue
            
        if 'TOTAIS' in line or 'VALOR TOTAL' in line:
            in_items_section = False
            in_general_info = False
            print("✅ Saiu das seções de dados")
            break
        
        if in_items_section:
            if line.strip() and len(line.strip()) > 10:
                items_section_lines.append(line.strip())
                
        # Extrair valor do frete e desconto da seção de informações gerais
        if in_general_info:
            # Procura por padrões de valor monetário após "Tipo de frete:"
            if 'Tipo de frete:' in line or 'frete' in line.lower():
                print(f"🔍 Linha com frete: {line}")
                # Procura por valores monetários na linha
                valores_monetarios = re.findall(r'R\$\s*([\d.,]+)|([\d.,]+)\s*(?:R\$)?', line)
                for match in valores_monetarios:
                    for valor in match:
                        if valor:
                            try:
                                # Tenta converter o primeiro valor monetário encontrado
                                valor_limpo = valor.replace('.', '').replace(',', '.')
                                valor_frete = float(valor_limpo)
                                print(f"💰 Valor do frete encontrado: R$ {valor_frete}")
                                break
                            except ValueError:
                                continue
                    if valor_frete > 0:
                        break
            
            # Procura por desconto financeiro
            if 'desconto' in line.lower() and ('financeiro' in line.lower() or 'R$' in line):
                print(f"🔍 Linha com desconto: {line}")
                # Procura por valores monetários na linha do desconto
                valores_monetarios = re.findall(r'R\$\s*([\d.,]+)|([\d.,]+)\s*(?:R\$)?', line)
                for match in valores_monetarios:
                    for valor in match:
                        if valor:
                            try:
                                valor_limpo = valor.replace('.', '').replace(',', '.')
                                valor_desconto = float(valor_limpo)
                                print(f"💰 Valor do desconto encontrado: R$ {valor_desconto}")
                                break
                            except ValueError:
                                continue
                    if valor_desconto > 0:
                        break
    
    print(f"Encontradas {len(items_section_lines)} linhas na seção de itens")
    
    for line in items_section_lines:
        print(f"🔍 Processando linha: {line}")
        
        pattern = r'(\d+)\s+(\d{10,})\s+(.*?)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)'
        
        match = re.search(pattern, line)
        if match:
            try:
                numero_item = match.group(1)
                codigo = match.group(2).strip()
                descricao = match.group(3).strip()
                quantidade = match.group(4).replace(',', '.')
                preco_unitario = match.group(5).replace(',', '.')
                icms_pct = match.group(6).replace(',', '.')
                ipi = match.group(7).replace(',', '.')
                preco_unitario_c_ipi = match.group(8).replace(',', '.')
                valor_total_c_ipi = match.group(9).replace(',', '.')
                
                qtd_float = float(quantidade)
                preco_unit_float = float(preco_unitario)
                preco_unit_c_ipi_float = float(preco_unitario_c_ipi)
                icms_pct_float = float(icms_pct)
                
                print(f"✅ Extraído - Código: {codigo}")
                print(f"   Qtd: {qtd_float}")
                print(f"   Preço unit. (sem IPI): {preco_unit_float}")
                print(f"   Preço unit. c/ IPI: {preco_unit_c_ipi_float}")
                print(f"   %ICMS: {icms_pct_float}%")
                
                if qtd_float > 0 and preco_unit_float > 0 and preco_unit_c_ipi_float > 0:
                    
                    codigo_encontrado = None
                    for codigo_variante in [codigo, codigo[:6], codigo[-6:]]:
                        if codigo_variante in PRODUCT_DB:
                            codigo_encontrado = codigo_variante
                            break
                    
                    items.append({
                        'codigo': codigo_encontrado or codigo,
                        'qtd': str(qtd_float),
                        'valor_unit': round(preco_unit_float, 2),
                        'valor_unit_c_ipi': round(preco_unit_c_ipi_float, 2),
                        'icms_pct': icms_pct_float
                    })
                    print("   ✅ Produto adicionado com sucesso!")
                        
            except (ValueError, IndexError) as e:
                print(f"❌ Erro ao processar linha: {e}")
                continue
    
    if not items:
        print("🔄 Tentando método alternativo...")
        for line in items_section_lines:
            codigos = re.findall(r'(\d{10,})', line)
            if not codigos:
                continue
                
            codigo = codigos[0]
            
            valores = re.findall(r'(\d+[,.]\d+)', line)
            if len(valores) >= 5:
                try:
                    qtd = float(valores[0].replace(',', '.'))
                    preco_sem_ipi = float(valores[1].replace(',', '.'))
                    icms_pct = float(valores[2].replace(',', '.'))
                    preco_com_ipi = None
                    
                    for i in range(3, min(6, len(valores))):
                        valor_teste = float(valores[i].replace(',', '.'))
                        if valor_teste > preco_sem_ipi:
                            preco_com_ipi = valor_teste
                            break
                    
                    if preco_com_ipi and qtd > 0:
                        items.append({
                            'codigo': codigo,
                            'qtd': str(qtd),
                            'valor_unit': round(preco_sem_ipi, 2),
                            'valor_unit_c_ipi': round(preco_com_ipi, 2),
                            'icms_pct': icms_pct
                        })
                        print(f"✅ Alternativo - Código: {codigo}, Preço c/IPI: {preco_com_ipi}, %ICMS: {icms_pct}")
                        
                except (ValueError, IndexError) as e:
                    print(f"❌ Erro no método alternativo: {e}")
                    continue
    
    print(f"=== RESULTADO: {len(items)} itens extraídos ===")
    print(f"💰 VALOR DO FRETE EXTRAÍDO: R$ {valor_frete}")
    print(f"💰 VALOR DO DESCONTO EXTRAÍDO: R$ {valor_desconto}")
    
    # Retorna tanto os itens quanto o valor do frete e desconto
    return {
        'items': items,
        'valor_frete': valor_frete,
        'valor_desconto': valor_desconto
    }

def extract_table_with_pdfplumber(file_path):
    try:
        import pdfplumber
        items = []
        valor_frete = 0.0
        valor_desconto = 0.0
        
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                print(f"=== Processando página {page_num + 1} ===")
                
                # Extrair texto para buscar informações gerais
                text = page.extract_text()
                if text:
                    # Buscar valor do frete no texto
                    if 'Tipo de frete:' in text or 'frete' in text.lower():
                        lines = text.split('\n')
                        for line in lines:
                            if 'Tipo de frete:' in line or 'frete' in line.lower():
                                print(f"🔍 Linha com frete: {line}")
                                valores_monetarios = re.findall(r'R\$\s*([\d.,]+)|([\d.,]+)\s*(?:R\$)?', line)
                                for match in valores_monetarios:
                                    for valor in match:
                                        if valor:
                                            try:
                                                valor_limpo = valor.replace('.', '').replace(',', '.')
                                                valor_frete = float(valor_limpo)
                                                print(f"💰 Valor do frete encontrado: R$ {valor_frete}")
                                                break
                                            except ValueError:
                                                continue
                                    if valor_frete > 0:
                                        break
                    
                    # Buscar valor do desconto no texto
                    if 'desconto' in text.lower():
                        lines = text.split('\n')
                        for line in lines:
                            if 'desconto' in line.lower() and ('financeiro' in line.lower() or 'R$' in line):
                                print(f"🔍 Linha com desconto: {line}")
                                valores_monetarios = re.findall(r'R\$\s*([\d.,]+)|([\d.,]+)\s*(?:R\$)?', line)
                                for match in valores_monetarios:
                                    for valor in match:
                                        if valor:
                                            try:
                                                valor_limpo = valor.replace('.', '').replace(',', '.')
                                                valor_desconto = float(valor_limpo)
                                                print(f"💰 Valor do desconto encontrado: R$ {valor_desconto}")
                                                break
                                            except ValueError:
                                                continue
                                    if valor_desconto > 0:
                                        break
                
                tables = page.extract_tables()
                print(f"Encontradas {len(tables)} tabelas")
                
                for table_idx, table in enumerate(tables):
                    print(f"📊 Tabela {table_idx + 1} com {len(table)} linhas")
                    
                    for row_idx, row in enumerate(table):
                        if not row or len(row) < 5:
                            continue
                            
                        clean_row = [str(cell).strip() for cell in row if cell and str(cell).strip()]
                        
                        codigo = None
                        for cell in clean_row:
                            if re.match(r'^\d{10,}$', cell):
                                codigo = cell
                                break
                        
                        if codigo:
                            print(f"🔍 Linha {row_idx}: Código {codigo}")
                            print(f"   Valores: {clean_row}")
                            
                            valores = []
                            for cell in clean_row:
                                if re.match(r'^\d+[,.]\d+$', cell):
                                    valores.append(cell)
                            
                            if len(valores) >= 4:
                                try:
                                    qtd = float(valores[0].replace(',', '.'))
                                    preco_sem_ipi = float(valores[1].replace(',', '.'))
                                    icms_pct = float(valores[2].replace(',', '.'))
                                    preco_com_ipi = None
                                    
                                    for i in range(3, min(7, len(valores))):
                                        valor_teste = float(valores[i].replace(',', '.'))
                                        if valor_teste > preco_sem_ipi:
                                            preco_com_ipi = valor_teste
                                            break
                                    
                                    if preco_com_ipi and qtd > 0:
                                        items.append({
                                            'codigo': codigo,
                                            'qtd': str(qtd),
                                            'valor_unit': round(preco_sem_ipi, 2),
                                            'valor_unit_c_ipi': round(preco_com_ipi, 2),
                                            'icms_pct': icms_pct
                                        })
                                        print(f"✅ Adicionado: {codigo}, Preço c/IPI: {preco_com_ipi}, %ICMS: {icms_pct}")
                                        
                                except ValueError as e:
                                    print(f"❌ Erro nos valores: {e}")
        
        return {
            'items': items,
            'valor_frete': valor_frete,
            'valor_desconto': valor_desconto
        }
    except Exception as e:
        print(f"❌ Erro com pdfplumber: {e}")
        return {'items': [], 'valor_frete': 0.0, 'valor_desconto': 0.0}

@app.route("/upload-base", methods=["POST"])
@login_required
def upload_base():
    file = request.files["file"]
    if not file:
        flash("Nenhum arquivo enviado", "danger")
        return redirect(url_for("site"))
    file_path = os.path.join("uploads", file.filename)
    file.save(file_path)
    try:
        load_product_base(file_path)
        flash("Base de produtos carregada e salva com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao carregar base: {e}", "danger")
    return redirect(url_for("site"))

@app.route("/remove-base", methods=["POST"])
@login_required
def remove_base():
    global PRODUCT_DB
    try:
        if os.path.exists(PRODUCT_BASE_FILE):
            os.remove(PRODUCT_BASE_FILE)
        PRODUCT_DB = {}
        flash("Base de produtos removida com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao remover base: {e}", "danger")
    return redirect(url_for("site"))

@app.route("/upload-pdf", methods=["POST"])
@login_required
def upload_pdf():
    file = request.files.get("pdf_file")
    if not file:
        flash("Nenhum arquivo enviado!", "danger")
        return redirect(url_for("site"))

    file_path = os.path.join("uploads", file.filename)
    file.save(file_path)

    extracted_data = {}
    valor_frete = 0.0
    valor_desconto = 0.0

    try:
        extracted_data = extract_table_with_pdfplumber(file_path)
        
        if not extracted_data['items']:
            text = extract_text_from_pdf(file_path)
            if text.strip():
                extracted_data = parse_extracted_text(text)
        
        extracted_items = extracted_data.get('items', [])
        valor_frete = extracted_data.get('valor_frete', 0.0)
        valor_desconto = extracted_data.get('valor_desconto', 0.0)
        
        if extracted_items:
            flash(f"✅ {len(extracted_items)} produto(s) identificado(s) automaticamente!", "success")
            if valor_frete > 0:
                flash(f"💰 Valor do frete identificado: R$ {valor_frete:.2f}", "info")
            if valor_desconto > 0:
                flash(f"💰 Valor do desconto identificado: R$ {valor_desconto:.2f}", "info")
        else:
            flash("⚠️ Nenhum produto identificado. Verifique se o PDF contém tabela legível.", "warning")
        
    except Exception as e:
        flash(f"Erro ao processar arquivo: {str(e)}", "danger")
        print(f"Erro detalhado: {e}")
    
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    ufs = sorted(STATE_RATES.keys())
    return render_template(
        "site.html",
        ufs=ufs,
        produtos=PRODUCT_DB,
        produtos_extraidos=extracted_items,
        valor_frete_encontrado=valor_frete,  # Passa o valor do frete para o template
        valor_desconto_encontrado=valor_desconto,  # Passa o valor do desconto para o template
        state_rates=STATE_RATES,
        username=session.get('username', 'Usuário')
    )

@app.route("/compute", methods=["POST"])
@login_required
def compute():
    if not PRODUCT_DB:
        flash("Nenhuma base de produtos carregada!", "danger")
        return redirect(url_for("site"))

    destino_uf = request.form["destino_uf"].strip().upper()
    aliquota_interna = STATE_RATES[destino_uf]

    items_out = []
    total_difal = Decimal("0.00")
    total_base_calculo = Decimal("0.00")

    codigos = request.form.getlist("codigo[]")
    valores_unit = request.form.getlist("valor_unit[]")
    valores_unit_c_ipi = request.form.getlist("preco_unit_c_ipi[]")
    quantidades = request.form.getlist("qtd[]")
    icms_pcts = request.form.getlist("icms_pct[]")
    is_uso_consumo = "uso-consumo" in request.form

    # MELHORIA ADICIONADA: Conversão correta de valores com vírgula para Decimal
    def safe_decimal_convert(value, default="0.00"):
        """Converte valores com vírgula para Decimal de forma segura"""
        if not value:
            return Decimal(default)
        try:
            # Remove possíveis R$ e espaços, substitui vírgula por ponto
            cleaned_value = str(value).replace('R$', '').replace(' ', '').replace(',', '.')
            return Decimal(cleaned_value)
        except:
            return Decimal(default)

    # Novos campos para frete, seguro, outros e desconto - com conversão segura
    valor_frete = safe_decimal_convert(request.form.get("valor_frete", "0.00"))
    valor_seguro = safe_decimal_convert(request.form.get("valor_seguro", "0.00"))
    valor_outros = safe_decimal_convert(request.form.get("valor_outros", "0.00"))
    valor_desconto = safe_decimal_convert(request.form.get("valor_desconto", "0.00"))

    print(f"🔍 DEBUG COMPUTE:")
    print(f"  UF Destino: {destino_uf}")
    print(f"  Alíquota Interna: {aliquota_interna}%")
    print(f"  Frete: R$ {valor_frete}")
    print(f"  Seguro: R$ {valor_seguro}")
    print(f"  Outros: R$ {valor_outros}")
    print(f"  Desconto: R$ {valor_desconto}")

    # Primeiro: calcular o valor total dos produtos
    valor_total_produtos = Decimal("0.00")
    
    for i in range(len(codigos)):
        codigo = codigos[i]
        qtd = safe_decimal_convert(quantidades[i])
        produto = PRODUCT_DB.get(codigo)

        if not produto:
            flash(f"Produto {codigo} não encontrado na base!", "danger")
            return redirect(url_for("site"))

        origem = produto["origem"]
        
        if is_uso_consumo:
            if i < len(valores_unit_c_ipi) and valores_unit_c_ipi[i]:
                valor_unit = safe_decimal_convert(valores_unit_c_ipi[i])
            else:
                valor_unit = safe_decimal_convert(valores_unit[i])
            tipo_valor = "c/IPI"
        else:
            valor_unit = safe_decimal_convert(valores_unit[i])
            tipo_valor = "normal"
        
        # Calcula valor total do produto
        valor_total_produto = valor_unit * qtd
        valor_total_produtos += valor_total_produto

        print(f"🔍 Produto {i+1}: {codigo}")
        print(f"   Origem: {origem}")
        print(f"   Valor Unitário: R$ {valor_unit}")
        print(f"   Quantidade: {qtd}")
        print(f"   Valor Total Produto: R$ {valor_total_produto}")

        valor_unit_c_ipi = None
        if i < len(valores_unit_c_ipi) and valores_unit_c_ipi[i]:
            valor_unit_c_ipi = safe_decimal_convert(valores_unit_c_ipi[i])

        # Pega o % ICMS do documento apenas para exibição
        icms_pct_doc = safe_decimal_convert(icms_pcts[i]) if i < len(icms_pcts) and icms_pcts[i] else Decimal("0.0")

        items_out.append({
            "codigo": codigo,
            "descricao": produto["descricao"],
            "origem": origem,
            "valor_unit": float(valor_unit),
            "valor_unit_c_ipi": float(valor_unit_c_ipi) if valor_unit_c_ipi else None,
            "qtd": float(qtd),
            "valor_total_produto": float(valor_total_produto),
            "tipo_valor": tipo_valor,
            "icms_pct_doc": float(icms_pct_doc)
        })

    print(f"💰 VALOR TOTAL PRODUTOS: R$ {valor_total_produtos}")
    print(f"💰 VALOR FRETE: R$ {valor_frete}")
    print(f"💰 VALOR SEGURO: R$ {valor_seguro}")
    print(f"💰 VALOR OUTROS: R$ {valor_outros}")
    print(f"💰 VALOR DESCONTO: R$ {valor_desconto}")

    # CALCULAR A BASE DE CÁLCULO TOTAL INCLUINDO FRETE, SEGURO, OUTROS E DESCONTO
    total_base_calculo = valor_total_produtos + valor_frete + valor_seguro + valor_outros - valor_desconto
    print(f"💰 BASE DE CÁLCULO TOTAL (Produtos + Frete + Seguro + Outros - Desconto): R$ {total_base_calculo}")

    # Segundo: calcular o DIFAL considerando frete, seguro, outros e desconto
    # Para cálculo proporcional por produto
    for i, item in enumerate(items_out):
        produto = PRODUCT_DB.get(item["codigo"])
        origem = produto["origem"]
        
        # Calcula a proporção que este produto representa no total
        if valor_total_produtos > 0:
            proporcao = Decimal(str(item["valor_total_produto"])) / valor_total_produtos
        else:
            proporcao = Decimal("0.00")
        
        # Distribui frete, seguro, outros e desconto proporcionalmente
        frete_proporcional = valor_frete * proporcao
        seguro_proporcional = valor_seguro * proporcao
        outros_proporcional = valor_outros * proporcao
        desconto_proporcional = valor_desconto * proporcao
        
        # Base de cálculo para este produto (INCLUINDO FRETE, SEGURO, OUTROS E DESCONTO)
        base_calculo_produto = (Decimal(str(item["valor_total_produto"])) + 
                               frete_proporcional + 
                               seguro_proporcional + 
                               outros_proporcional - 
                               desconto_proporcional)
        
        # Garante que a base de cálculo não seja negativa
        if base_calculo_produto < 0:
            base_calculo_produto = Decimal("0.00")
        
        # Calcula DIFAL para este produto usando a função calc_difal atualizada
        difal_pct, valor_difal_produto, base_calculo = calc_difal(
            Decimal(str(item["valor_total_produto"])),
            frete_proporcional,
            seguro_proporcional, 
            outros_proporcional,
            desconto_proporcional,
            origem, 
            aliquota_interna
        )
        
        # Atualiza o item com os valores calculados
        items_out[i]["difal_pct"] = float(difal_pct)
        items_out[i]["valor_difal_total"] = float(valor_difal_produto)
        items_out[i]["base_calculo"] = float(base_calculo_produto)
        items_out[i]["frete_proporcional"] = float(frete_proporcional)
        items_out[i]["seguro_proporcional"] = float(seguro_proporcional)
        items_out[i]["outros_proporcional"] = float(outros_proporcional)
        items_out[i]["desconto_proporcional"] = float(desconto_proporcional)
        
        total_difal += valor_difal_produto
        
        print(f"🔍 DIFAL Produto {i+1}:")
        print(f"   Proporção: {proporcao:.4f}")
        print(f"   Frete Proporcional: R$ {frete_proporcional}")
        print(f"   Seguro Proporcional: R$ {seguro_proporcional}")
        print(f"   Outros Proporcional: R$ {outros_proporcional}")
        print(f"   Desconto Proporcional: R$ {desconto_proporcional}")
        print(f"   Base Cálculo Produto: R$ {base_calculo_produto}")
        print(f"   DIFAL Calculado: {difal_pct}% = R$ {valor_difal_produto}")

    print(f"💰 TOTAL BASE CÁLCULO: R$ {total_base_calculo}")
    print(f"💰 TOTAL DIFAL: R$ {total_difal}")

    return render_template(
        "resultado.html",
        destino_uf=destino_uf,
        aliquota_interna=aliquota_interna,
        items=items_out,
        total_difal=float(total_difal),
        total_base_calculo=float(total_base_calculo),
        valor_frete=float(valor_frete),
        valor_seguro=float(valor_seguro),
        valor_outros=float(valor_outros),
        valor_desconto=float(valor_desconto),
        is_uso_consumo=is_uso_consumo,
        username=session.get('username', 'Usuário'),
        now=datetime.now()
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 1500))
    app.run(host="0.0.0.0", port=port, debug=True)