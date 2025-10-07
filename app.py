from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func, case
from sqlalchemy import or_
from collections import defaultdict
from datetime import date, datetime, timedelta
import os
import io
import pandas as pd
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
from flask import send_file
app = Flask(__name__)

# --- CONFIGURAÇÕES GERAIS ---
# Para o Render, estas chaves virão das Variáveis de Ambiente
# Para o modo local, o app.config abaixo funcionará
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'uma-chave-secreta-local-muito-dificil')
# Lê a URL do banco de dados do PythonAnywhere, se existir.
# Se não, usa o arquivo local 'quiz.db' como padrão.
database_uri = os.environ.get('DATABASE_URL_PYTHONANYWHERE', 'sqlite:///quiz.db')
app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'xls', 'xlsx'}

# --- CONFIGURAÇÃO DO CLOUDINARY (Lê das Variáveis de Ambiente) ---
cloudinary.config(
    cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key = os.environ.get('CLOUDINARY_API_KEY'),
    api_secret = os.environ.get('CLOUDINARY_API_SECRET')
)

# --- INICIALIZAÇÕES ---
db = SQLAlchemy(app)
SENHA_ADMIN = "admin123"

# --- TABELA DE LIGAÇÃO (MUITOS-PARA-MUITOS) ---
pergunta_departamento_association = db.Table('pergunta_departamento',
    db.Column('pergunta_id', db.Integer, db.ForeignKey('pergunta.id'), primary_key=True),
    db.Column('departamento_id', db.Integer, db.ForeignKey('departamento.id'), primary_key=True)
)

# --- MODELOS DO BANCO DE DADOS ---
class Departamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    usuarios = db.relationship('Usuario', backref='departamento', lazy=True)

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    codigo_acesso = db.Column(db.String(4), unique=True, nullable=False)
    departamento_id = db.Column(db.Integer, db.ForeignKey('departamento.id'), nullable=False)
    respostas = db.relationship('Resposta', backref='usuario', lazy=True)

class Pergunta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False, default='multipla_escolha')
    texto = db.Column(db.String(500), nullable=False)
    opcao_a = db.Column(db.String(500), nullable=True)
    opcao_b = db.Column(db.String(500), nullable=True)
    opcao_c = db.Column(db.String(500), nullable=True)
    opcao_d = db.Column(db.String(500), nullable=True)
    resposta_correta = db.Column(db.String(1), nullable=True)
    data_liberacao = db.Column(db.Date, nullable=False)
    tempo_limite = db.Column(db.Integer, nullable=True)
    imagem_pergunta = db.Column(db.String(300), nullable=True)
    para_todos_setores = db.Column(db.Boolean, default=False, nullable=False)
    departamentos = db.relationship('Departamento', secondary=pergunta_departamento_association, lazy='subquery',
        backref=db.backref('perguntas', lazy=True))

class Resposta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pontos = db.Column(db.Integer, nullable=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    pergunta_id = db.Column(db.Integer, db.ForeignKey('pergunta.id'), nullable=False)
    resposta_dada = db.Column(db.String(1), nullable=True)
    data_resposta = db.Column(db.DateTime, default=datetime.utcnow)
    pergunta = db.relationship('Pergunta')
    texto_discursivo = db.Column(db.Text, nullable=True)
    anexo_resposta = db.Column(db.String(300), nullable=True)
    status_correcao = db.Column(db.String(20), nullable=False, default='nao_respondido')
    feedback_admin = db.Column(db.Text, nullable=True)
    feedback_visto = db.Column(db.Boolean, default=False, nullable=False)

# --- FUNÇÕES AUXILIARES ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


@app.template_filter('datetime_local')
def format_datetime_local(valor_utc):
    """Filtro para converter uma data UTC para o fuso local (UTC-3) e formatá-la."""
    if not valor_utc:
        return ""
    # Subtrai 3 horas do tempo UTC
    fuso_local = valor_utc - timedelta(hours=3)
    return fuso_local.strftime('%d/%m/%Y às %H:%M')

def get_texto_da_opcao(pergunta, opcao):
    if opcao == 'a': return pergunta.opcao_a
    if opcao == 'b': return pergunta.opcao_b
    if opcao == 'c': return pergunta.opcao_c
    if opcao == 'd': return pergunta.opcao_d
    if opcao == 'v': return "Verdadeiro"
    if opcao == 'f': return "Falso"
    return ""

@app.context_processor
def utility_processor():
    return dict(get_texto_da_opcao=get_texto_da_opcao)

def validar_linha(row):
    errors = {}
    if not row.get('texto'): errors['texto'] = "O texto não pode ser vazio."
    tipo = str(row.get('tipo') or '').lower()
    if tipo not in ['multipla_escolha', 'verdadeiro_falso', 'discursiva']:
        errors['tipo'] = "Tipo inválido."
    resposta = str(row.get('resposta_correta') or '').lower()
    if tipo == 'multipla_escolha' and resposta not in ['a', 'b', 'c', 'd']:
        errors['resposta_correta'] = "Deve ser a, b, c ou d."
    elif tipo == 'verdadeiro_falso' and resposta not in ['v', 'f']:
        errors['resposta_correta'] = "Deve ser v ou f."
    try:
        if isinstance(row.get('data_liberacao'), datetime):
             row['data_liberacao'] = row['data_liberacao'].strftime('%d/%m/%Y')
        datetime.strptime(str(row.get('data_liberacao', '')), '%d/%m/%Y').date()
    except (ValueError, TypeError):
        errors['data_liberacao'] = "Formato inválido. Use DD/MM/AAAA."
    if tipo != 'discursiva':
        try:
            int(float(row.get('tempo_limite', '')))
        except (ValueError, TypeError):
            errors['tempo_limite'] = "Deve ser um número."
    is_valid = not errors
    return is_valid, errors

def _gerar_dados_relatorio(departamento_id=None):
    """Função auxiliar que busca e processa os dados para o relatório."""
    query = db.session.query(
        Usuario.nome,
        Departamento.nome.label('setor_nome'),
        func.count(Resposta.id).label('total_respostas'),
        func.sum(case((or_(Resposta.pontos > 0, Resposta.status_correcao.in_(['correto', 'parcialmente_correto'])), 1), else_=0)).label('respostas_corretas'),
        func.coalesce(func.sum(Resposta.pontos), 0).label('pontuacao_total')
    ).select_from(Usuario).join(Departamento).outerjoin(Resposta).group_by(
        # MUDANÇA: Adicionamos Departamento.nome ao GROUP BY
        Usuario.id, Departamento.nome
    )

    if departamento_id:
        query = query.filter(Usuario.departamento_id == departamento_id)

    resultados = query.order_by(Usuario.nome).all()

    relatorios_finais = []
    for resultado in resultados:
        aproveitamento = (resultado.respostas_corretas / resultado.total_respostas) * 100 if resultado.total_respostas > 0 else 0
        relatorios_finais.append({
            'nome': resultado.nome,
            'setor': resultado.setor_nome,
            'total_respostas': resultado.total_respostas,
            'respostas_corretas': resultado.respostas_corretas,
            'aproveitamento': aproveitamento,
            'pontuacao_total': resultado.pontuacao_total
        })
    return relatorios_finais

# --- ROTAS PRINCIPAIS DO USUÁRIO ---
@app.route('/')
def pagina_login():
    if 'usuario_id' in session: return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def processa_login():
    codigo_inserido = request.form['codigo']
    usuario = Usuario.query.filter_by(codigo_acesso=codigo_inserido).first()
    if usuario:
        session['usuario_id'], session['usuario_nome'] = usuario.id, usuario.nome
        return redirect(url_for('dashboard'))
    else:
        flash('Código de acesso inválido!', 'danger')
        return redirect(url_for('pagina_login'))


@app.route('/dashboard')
def dashboard():
    if 'usuario_id' not in session: 
        return redirect(url_for('pagina_login'))

    usuario_id = session['usuario_id']
    usuario = Usuario.query.get(usuario_id)
    hoje = date.today()
    
    perguntas_respondidas_ids = [r.pergunta_id for r in Resposta.query.filter_by(usuario_id=usuario_id).all()]
    
    # Contagem de Quiz Rápido Pendente (sem mudanças)
    contagem_quiz_pendente = Pergunta.query.filter(
        Pergunta.tipo != 'discursiva',
        Pergunta.data_liberacao <= hoje,
        Pergunta.id.notin_(perguntas_respondidas_ids),
        or_(
            Pergunta.para_todos_setores == True,
            Pergunta.departamentos.any(Departamento.id == usuario.departamento_id)
        )
    ).count()

    # Contagem de Atividades Discursivas Pendentes (sem mudanças)
    contagem_atividades_pendentes = Pergunta.query.filter(
        Pergunta.tipo == 'discursiva',
        Pergunta.data_liberacao <= hoje,
        Pergunta.id.notin_(perguntas_respondidas_ids),
        or_(
            Pergunta.para_todos_setores == True,
            Pergunta.departamentos.any(Departamento.id == usuario.departamento_id)
        )
    ).count()

    # MUDANÇA: Contagem de feedbacks agora verifica a nova coluna 'feedback_visto'
    contagem_novos_feedbacks = Resposta.query.join(Pergunta).filter(
        Resposta.usuario_id == usuario_id,
        Pergunta.tipo == 'discursiva',
        Resposta.status_correcao.in_(['correto', 'incorreto']),
        Resposta.feedback_visto == False  # Só conta se ainda não foi visto
    ).count()
    
    return render_template('dashboard.html', 
                           nome=session['usuario_nome'],
                           contagem_quiz=contagem_quiz_pendente,
                           contagem_atividades=contagem_atividades_pendentes,
                           contagem_feedbacks=contagem_novos_feedbacks)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('pagina_login'))

@app.route('/quiz')
def pagina_quiz():
    if 'usuario_id' not in session: return redirect(url_for('pagina_login'))
    usuario_id = session['usuario_id']
    usuario = Usuario.query.get(usuario_id)
    hoje = date.today()
    perguntas_respondidas_ids = [r.pergunta_id for r in Resposta.query.filter_by(usuario_id=usuario_id).all()]
    proxima_pergunta = Pergunta.query.filter(
        Pergunta.tipo != 'discursiva',
        Pergunta.data_liberacao <= hoje,
        Pergunta.id.notin_(perguntas_respondidas_ids),
        or_(
            Pergunta.para_todos_setores == True,
            Pergunta.departamentos.any(Departamento.id == usuario.departamento_id)
        )
    ).order_by(Pergunta.data_liberacao).first()
    if proxima_pergunta:
        return render_template('quiz.html', pergunta=proxima_pergunta)
    else:
        flash('Parabéns, você respondeu todas as perguntas de quiz rápido disponíveis para o seu setor!', 'success')
        return redirect(url_for('dashboard'))

@app.route('/atividades')
def pagina_atividades():
    if 'usuario_id' not in session: return redirect(url_for('pagina_login'))
    hoje = date.today()
    usuario_id = session['usuario_id']
    usuario = Usuario.query.get(usuario_id)
    atividades = Pergunta.query.filter(
        Pergunta.tipo == 'discursiva',
        Pergunta.data_liberacao <= hoje,
        or_(
            Pergunta.para_todos_setores == True,
            Pergunta.departamentos.any(Departamento.id == usuario.departamento_id)
        )
    ).order_by(Pergunta.data_liberacao.desc()).all()
    respostas_dadas = {r.pergunta_id: r for r in Resposta.query.filter_by(usuario_id=usuario_id).all()}
    return render_template('atividades.html', atividades=atividades, respostas_dadas=respostas_dadas)

# Dentro de app.py

@app.route('/atividade/<int:pergunta_id>', methods=['GET', 'POST'])
def responder_atividade(pergunta_id):
    if 'usuario_id' not in session: 
        return redirect(url_for('pagina_login'))

    pergunta = Pergunta.query.get_or_404(pergunta_id)

    if request.method == 'POST':
        texto_resposta = request.form['texto_discursivo']
        
        # ====================================================================
        # A LÓGICA PARA PROCESSAR O ANEXO ESTÁ AQUI
        # ====================================================================
        anexo_url = None # 1. Começa sem anexo por padrão.
        
        if 'anexo_resposta' in request.files: # 2. Verifica se um arquivo foi enviado.
            file = request.files['anexo_resposta']
            if file and file.filename != '' and allowed_file(file.filename):
                # 3. Envia o arquivo para o Cloudinary de forma segura.
                #    'resource_type="auto"' permite enviar PDFs, Docs, etc., além de imagens.
                upload_result = cloudinary.uploader.upload(file, resource_type="auto")
                # 4. Pega a URL segura que o Cloudinary devolveu.
                anexo_url = upload_result.get('secure_url')
        # ====================================================================

        # 5. Salva a resposta no banco de dados com o link do anexo (ou None se não houver).
        nova_resposta = Resposta(
            usuario_id=session['usuario_id'],
            pergunta_id=pergunta.id,
            texto_discursivo=texto_resposta,
            anexo_resposta=anexo_url, # <-- A URL é salva aqui
            status_correcao='pendente'
        )
        db.session.add(nova_resposta)
        db.session.commit()
        
        flash('Sua resposta foi enviada para avaliação!', 'success')
        return redirect(url_for('pagina_atividades'))

    # Se a requisição for GET, apenas mostra a página.
    return render_template('atividade_responder.html', pergunta=pergunta)

@app.route('/responder', methods=['POST'])
def processa_resposta():
    if 'usuario_id' not in session: return redirect(url_for('pagina_login'))
    pergunta_id = request.form['pergunta_id']
    resposta_usuario = request.form.get('resposta', '')
    pergunta = Pergunta.query.get(pergunta_id)
    pontos = 0
    if pergunta.resposta_correta == resposta_usuario:
        tempo_restante = float(request.form['tempo_restante'])
        pontos = 100 + int(tempo_restante * 5)
        flash(f'Resposta correta! Você ganhou {pontos} pontos.', 'success')
    else:
        flash('Resposta incorreta. Sem pontos desta vez.', 'danger')
    nova_resposta = Resposta(
        pontos=pontos, 
        usuario_id=session['usuario_id'], 
        pergunta_id=pergunta_id, 
        resposta_dada=resposta_usuario, 
        status_correcao='correto' if pontos > 0 else 'incorreto'
    )
    db.session.add(nova_resposta)
    db.session.commit()
    return redirect(url_for('pagina_quiz'))

# Em app.py

@app.route('/minhas-respostas')
def minhas_respostas():
    if 'usuario_id' not in session: 
        return redirect(url_for('pagina_login'))

    usuario_id = session['usuario_id']

    # --- INÍCIO DA NOVA LÓGICA: Marcar feedbacks como vistos ---
    # Esta ação acontece toda vez que o usuário visita a página, "limpando" os avisos.
    feedbacks_nao_vistos = Resposta.query.join(Pergunta).filter(
        Resposta.usuario_id == usuario_id,
        Pergunta.tipo == 'discursiva',
        Resposta.status_correcao.in_(['correto', 'incorreto']),
        Resposta.feedback_visto == False
    ).all()

    if feedbacks_nao_vistos:
        for resposta in feedbacks_nao_vistos:
            resposta.feedback_visto = True
        db.session.commit()
    # --- FIM DA NOVA LÓGICA ---
    
    # Pega os valores dos filtros da URL (se existirem)
    filtro_tipo = request.args.get('filtro_tipo', '')
    filtro_resultado = request.args.get('filtro_resultado', '')

    # Começa a busca base, pegando apenas as respostas do usuário logado
    query = Resposta.query.filter_by(usuario_id=usuario_id)

    # Aplica o filtro de TIPO DE PERGUNTA, se selecionado
    if filtro_tipo:
        query = query.join(Pergunta).filter(Pergunta.tipo == filtro_tipo)

    # Aplica o filtro de RESULTADO, se selecionado
    if filtro_resultado == 'corretas':
        # Uma resposta é correta se os pontos forem > 0 (objetivas) OU o status for 'correto' (discursivas)
        query = query.filter(or_(Resposta.pontos > 0, Resposta.status_correcao == 'correto'))
    elif filtro_resultado == 'incorretas':
        # É incorreta se os pontos forem 0 (objetivas) OU o status for 'incorreto' (discursivas)
        query = query.filter(or_(Resposta.pontos == 0, Resposta.status_correcao == 'incorreto'))
    elif filtro_resultado == 'pendentes':
        # Apenas para discursivas aguardando avaliação
        query = query.filter(Resposta.status_correcao == 'pendente')
    
    # Executa a busca final com os filtros e ordena pelas mais recentes
    respostas_usuario = query.order_by(Resposta.data_resposta.desc()).all()

    return render_template('minhas_respostas.html', 
                           respostas=respostas_usuario,
                           filtro_tipo=filtro_tipo,
                           filtro_resultado=filtro_resultado)

# Em app.py

# Em app.py

@app.route('/admin/relatorios/exportar_detalhado')
def exportar_respostas_detalhado():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('pagina_admin'))

    depto_selecionado_id = request.args.get('departamento_id', type=int)
    # Pega o tipo de relatório a ser gerado (quiz ou discursivas)
    tipo_relatorio = request.args.get('tipo', 'todos')

    # Busca base de todas as respostas
    query = Resposta.query.join(Usuario).join(Departamento).join(Pergunta)

    # Aplica o filtro de setor, se houver
    if depto_selecionado_id:
        query = query.filter(Usuario.departamento_id == depto_selecionado_id)
        
    # Aplica o filtro de TIPO de pergunta
    if tipo_relatorio == 'quiz':
        query = query.filter(Pergunta.tipo != 'discursiva')
    elif tipo_relatorio == 'discursivas':
        query = query.filter(Pergunta.tipo == 'discursiva')

    todas_as_respostas = query.order_by(Departamento.nome, Usuario.nome, Resposta.data_resposta).all()

    if not todas_as_respostas:
        flash("Nenhuma resposta encontrada para exportar com os filtros selecionados.", "warning")
        return redirect(url_for('pagina_analytics'))

    # Processa os dados e cria a planilha
    dados_para_planilha = []
    colunas = []
    
    if tipo_relatorio == 'quiz':
        colunas = ['Colaborador', 'Setor', 'Data da Resposta', 'Pergunta', 'Tipo', 'Resposta Dada', 'Resposta Correta', 'Pontos']
        for r in todas_as_respostas:
            dados_para_planilha.append({
                'Colaborador': r.usuario.nome, 'Setor': r.usuario.departamento.nome,
                'Data da Resposta': (r.data_resposta - timedelta(hours=3)).strftime('%d/%m/%Y %H:%M'),
                'Pergunta': r.pergunta.texto, 'Tipo': r.pergunta.tipo,
                'Resposta Dada': get_texto_da_opcao(r.pergunta, r.resposta_dada),
                'Resposta Correta': get_texto_da_opcao(r.pergunta, r.pergunta.resposta_correta),
                'Pontos': r.pontos or 0
            })
    else: # Discursivas
        colunas = ['Colaborador', 'Setor', 'Data da Resposta', 'Pergunta', 'Resposta Discursiva', 'Status', 'Feedback', 'Pontos']
        for r in todas_as_respostas:
             dados_para_planilha.append({
                'Colaborador': r.usuario.nome, 'Setor': r.usuario.departamento.nome,
                'Data da Resposta': (r.data_resposta - timedelta(hours=3)).strftime('%d/%m/%Y %H:%M'),
                'Pergunta': r.pergunta.texto, 'Resposta Discursiva': r.texto_discursivo,
                'Status': r.status_correcao, 'Feedback': r.feedback_admin or '',
                'Pontos': r.pontos or 0
            })

    df = pd.DataFrame(dados_para_planilha, columns=colunas)
    output = io.BytesIO()
    
    nome_arquivo = f'relatorio_detalhado_{tipo_relatorio}.xlsx'

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Relatorio Detalhado')
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=nome_arquivo
    )

# --- ROTAS DE RANKING ---
@app.route('/ranking')
def pagina_ranking():
    if 'usuario_id' not in session: return redirect(url_for('pagina_login'))

    # MUDANÇA: Usamos func.coalesce para garantir que a soma nunca seja None
    pontos_por_depto = db.session.query(
        Departamento.nome,
        func.coalesce(func.sum(Resposta.pontos), 0).label('pontos_totais')
    ).join(Usuario, Departamento.id == Usuario.departamento_id).join(Resposta, Usuario.id == Resposta.usuario_id).group_by(Departamento.nome).all()

    usuarios_por_depto = db.session.query(
        Departamento.id, 
        Departamento.nome,
        func.count(Usuario.id).label('num_usuarios')
    ).join(Usuario, Departamento.id == Usuario.departamento_id).group_by(Departamento.id, Departamento.nome).all()

    ranking_final = []
    pontos_dict = dict(pontos_por_depto)
    
    for depto_id, depto_nome, num_usuarios in usuarios_por_depto:
        # Agora, a busca a partir de 'pontos_dict' sempre retornará um número
        pontos_totais = pontos_dict.get(depto_nome, 0)
        pontuacao_proporcional = pontos_totais / num_usuarios if num_usuarios > 0 else 0
        
        ranking_final.append({
            'id': depto_id, 
            'nome': depto_nome, 
            'pontos_totais': pontos_totais, 
            'num_usuarios': num_usuarios, 
            'pontuacao_proporcional': round(pontuacao_proporcional)
        })
        
    ranking_final.sort(key=lambda x: x['pontuacao_proporcional'], reverse=True)
    
    return render_template('ranking.html', ranking=ranking_final)

@app.route('/ranking/<int:departamento_id>')
def pagina_ranking_detalhe(departamento_id):
    if 'usuario_id' not in session: return redirect(url_for('pagina_login'))
    departamento = Departamento.query.get_or_404(departamento_id)
    ranking_individual_query = db.session.query(Usuario.nome, func.coalesce(func.sum(Resposta.pontos), 0).label('pontos_totais'), func.coalesce(func.count(Resposta.id), 0).label('total_respostas'), func.coalesce(func.sum(case((Resposta.pontos > 0, 1), else_=0)), 0).label('total_acertos')).select_from(Usuario).outerjoin(Resposta).filter(Usuario.departamento_id == departamento_id).group_by(Usuario.nome).all()
    ranking_final = []
    for membro in ranking_individual_query:
        total_respostas = membro.total_respostas
        total_acertos = membro.total_acertos
        percentual = (total_acertos / total_respostas) * 100 if total_respostas > 0 else 0
        ranking_final.append({'nome': membro.nome, 'pontos_totais': membro.pontos_totais, 'total_respostas': total_respostas, 'total_acertos': total_acertos, 'percentual_acertos': round(percentual, 1)})
    ranking_final.sort(key=lambda x: x['nome'])
    return render_template('ranking_detalhe.html', departamento=departamento, ranking=ranking_final)

# --- ROTAS DE ADMIN ---


@app.route('/admin', methods=['GET', 'POST'])
def pagina_admin():
    if 'csv_data' in session:
        session.pop('csv_data', None)
        session.pop('has_valid_rows', None)
        session.pop('csv_headers', None)

    senha_correta = session.get('admin_logged_in', False)
    if request.method == 'POST' and not senha_correta:
        if request.form.get('senha') == SENHA_ADMIN:
            session['admin_logged_in'] = True
            senha_correta = True
        else:
            flash('Senha incorreta!', 'danger')
    
    perguntas, usuarios, departamentos = [], [], []
    contagem_pendentes = 0
    
    # Dicionário para passar os valores dos filtros de volta para o template
    filtros_ativos = {}

    if senha_correta:
        # Busca inicial de dados para os formulários
        usuarios = Usuario.query.join(Departamento).order_by(Departamento.nome, Usuario.nome).all()
        departamentos = Departamento.query.order_by(Departamento.nome).all()
        contagem_pendentes = Resposta.query.join(Pergunta).filter(Pergunta.tipo == 'discursiva', Resposta.status_correcao == 'pendente').count()

        # --- INÍCIO DA NOVA LÓGICA DE FILTRAGEM DE PERGUNTAS ---
        
        # 1. Começa com uma busca base para todas as perguntas
        query_perguntas = Pergunta.query

        # 2. Pega os valores dos filtros da URL (se existirem)
        filtro_mes = request.args.get('filtro_mes') # Ex: '2025-10'
        filtro_setor_id = request.args.get('filtro_setor', type=int)
        filtro_tipo = request.args.get('filtro_tipo')

        # 3. Aplica os filtros na busca, um por um
        if filtro_mes:
            try:
                ano, mes = map(int, filtro_mes.split('-'))
                query_perguntas = query_perguntas.filter(
                    db.extract('year', Pergunta.data_liberacao) == ano,
                    db.extract('month', Pergunta.data_liberacao) == mes
                )
                filtros_ativos['mes'] = filtro_mes
            except:
                pass # Ignora filtro de data mal formatado

        if filtro_setor_id:
            query_perguntas = query_perguntas.filter(
                or_(
                    Pergunta.para_todos_setores == True,
                    Pergunta.departamentos.any(Departamento.id == filtro_setor_id)
                )
            )
            filtros_ativos['setor_id'] = filtro_setor_id

        if filtro_tipo:
            query_perguntas = query_perguntas.filter(Pergunta.tipo == filtro_tipo)
            filtros_ativos['tipo'] = filtro_tipo

        # 4. Executa a busca final com os filtros aplicados
        perguntas = query_perguntas.order_by(Pergunta.data_liberacao.desc(), Pergunta.id.desc()).all()
        # --- FIM DA NOVA LÓGICA DE FILTRAGEM ---

    return render_template('admin.html', 
                           senha_correta=senha_correta, 
                           perguntas=perguntas, 
                           usuarios=usuarios, 
                           departamentos=departamentos,
                           contagem_pendentes=contagem_pendentes,
                           filtros=filtros_ativos) # Envia os filtros ativos para o template

@app.route('/admin/add_department', methods=['POST'])
def adicionar_setor():
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    nome_setor = request.form.get('nome')
    if nome_setor and not Departamento.query.filter_by(nome=nome_setor).first():
        novo_depto = Departamento(nome=nome_setor)
        db.session.add(novo_depto)
        db.session.commit()
        flash(f'Setor "{nome_setor}" adicionado com sucesso!', 'success')
    else:
        flash(f'Erro: O nome do setor não pode ser vazio ou já existe.', 'danger')
    return redirect(url_for('pagina_admin'))

@app.route('/admin/delete_department/<int:departamento_id>', methods=['POST'])
def excluir_setor(departamento_id):
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    depto = Departamento.query.get_or_404(departamento_id)
    if depto.usuarios:
        flash(f'Não é possível excluir o setor "{depto.nome}" pois ele possui usuários.', 'danger')
    else:
        db.session.delete(depto)
        db.session.commit()
        flash(f'Setor "{depto.nome}" excluído com sucesso.', 'success')
    return redirect(url_for('pagina_admin'))

@app.route('/admin/add_user', methods=['POST'])
def adicionar_usuario():
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    
    codigo = request.form['codigo_acesso']
    email = request.form.get('email') # Usamos .get() para não dar erro se for vazio

    if Usuario.query.filter_by(codigo_acesso=codigo).first():
        flash(f'Erro: O código de acesso "{codigo}" já está em uso.', 'danger')
        return redirect(url_for('pagina_admin'))
    
    # MUDANÇA: A verificação de e-mail agora só acontece se um e-mail for digitado
    if email and Usuario.query.filter_by(email=email).first():
        flash(f'Erro: O e-mail "{email}" já está em uso.', 'danger')
        return redirect(url_for('pagina_admin'))
        
    novo_usuario = Usuario(
        nome=request.form['nome'],
        email=email or None, # Salva None se o campo estiver vazio
        codigo_acesso=codigo,
        departamento_id=request.form['departamento_id']
    )
    db.session.add(novo_usuario)
    db.session.commit()
    flash('Usuário adicionado com sucesso!', 'success')
    return redirect(url_for('pagina_admin'))

@app.route('/admin/edit_user/<int:usuario_id>', methods=['GET'])
def editar_usuario(usuario_id):
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    usuario = Usuario.query.get_or_404(usuario_id)
    departamentos = Departamento.query.order_by(Departamento.nome).all()
    return render_template('edit_user.html', usuario=usuario, departamentos=departamentos)

@app.route('/admin/edit_user/<int:usuario_id>', methods=['POST'])
def atualizar_usuario(usuario_id):
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    
    usuario = Usuario.query.get_or_404(usuario_id)
    novo_codigo = request.form['codigo_acesso']
    novo_email = request.form.get('email')

    codigo_existente = Usuario.query.filter(Usuario.id != usuario_id, Usuario.codigo_acesso == novo_codigo).first()
    if codigo_existente:
        flash(f'Erro: O código de acesso "{novo_codigo}" já está em uso por outro usuário.', 'danger')
        return redirect(url_for('editar_usuario', usuario_id=usuario_id))

    # MUDANÇA: A verificação de e-mail agora só acontece se um e-mail for digitado
    if novo_email and Usuario.query.filter(Usuario.id != usuario_id, Usuario.email == novo_email).first():
        flash(f'Erro: O e-mail "{novo_email}" já está em uso por outro usuário.', 'danger')
        return redirect(url_for('editar_usuario', usuario_id=usuario_id))

    usuario.nome = request.form['nome']
    usuario.email = novo_email or None # Salva None se o campo estiver vazio
    usuario.codigo_acesso = novo_codigo
    usuario.departamento_id = request.form['departamento_id']
    
    db.session.commit()
    flash(f'Usuário "{usuario.nome}" atualizado com sucesso!', 'success')
    return redirect(url_for('pagina_admin'))

@app.route('/admin/delete_user/<int:usuario_id>', methods=['POST'])
def excluir_usuario(usuario_id):
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    usuario = Usuario.query.get_or_404(usuario_id)
    Resposta.query.filter_by(usuario_id=usuario_id).delete()
    db.session.delete(usuario)
    db.session.commit()
    flash(f'Usuário "{usuario.nome}" e todas as suas respostas foram excluídos.', 'success')
    return redirect(url_for('pagina_admin'))

@app.route('/admin/add_question', methods=['POST'])
def adicionar_pergunta():
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    
    tipo = request.form.get('tipo')
    data_str = request.form.get('data_liberacao')
    data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()

    nova_pergunta = Pergunta(
        tipo=tipo,
        texto=request.form.get('texto'),
        data_liberacao=data_obj
    )

    # =========================================================
    # LÓGICA CORRIGIDA PARA USAR O CLOUDINARY
    # =========================================================
    if 'imagem_pergunta' in request.files:
        file = request.files['imagem_pergunta']
        if file and file.filename != '' and allowed_file(file.filename):
            # Envia o arquivo para a nuvem do Cloudinary
            upload_result = cloudinary.uploader.upload(file, folder="perguntas")
            # Pega a URL segura que o Cloudinary devolveu
            imagem_url = upload_result.get('secure_url')
            # Salva essa URL no banco de dados
            nova_pergunta.imagem_pergunta = imagem_url
    # =========================================================

    if 'para_todos_setores' in request.form:
        nova_pergunta.para_todos_setores = True
    else:
        nova_pergunta.para_todos_setores = False
        departamento_ids = request.form.getlist('departamentos')
        if departamento_ids:
            departamentos_selecionados = Departamento.query.filter(Departamento.id.in_(departamento_ids)).all()
            nova_pergunta.departamentos = departamentos_selecionados
    
    if tipo in ['multipla_escolha', 'verdadeiro_falso']:
        nova_pergunta.resposta_correta = request.form.get('resposta_correta')
        nova_pergunta.tempo_limite = request.form.get('tempo_limite')
        if tipo == 'multipla_escolha':
            nova_pergunta.opcao_a, nova_pergunta.opcao_b, nova_pergunta.opcao_c, nova_pergunta.opcao_d = request.form.get('opcao_a'), request.form.get('opcao_b'), request.form.get('opcao_c'), request.form.get('opcao_d')
    else: # Discursiva ou V/F (opções são nulas)
        nova_pergunta.tempo_limite, nova_pergunta.resposta_correta = None, None
        if tipo == 'discursiva': # Garante que opções M/E fiquem nulas
             nova_pergunta.opcao_a, nova_pergunta.opcao_b, nova_pergunta.opcao_c, nova_pergunta.opcao_d = None, None, None, None


    db.session.add(nova_pergunta)
    db.session.commit()
    flash('Pergunta adicionada com sucesso!', 'success')
    
    # A lógica de notificação foi desativada para a versão local
    # if 'enviar_notificacao' in request.form:
    #     disparar_notificacao_nova_pergunta(nova_pergunta)
    
    return redirect(url_for('pagina_admin'))

@app.route('/admin/edit_question/<int:pergunta_id>', methods=['GET'])
def editar_pergunta(pergunta_id):
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    pergunta = Pergunta.query.get_or_404(pergunta_id)
    todos_departamentos = Departamento.query.order_by(Departamento.nome).all()
    return render_template('edit_question.html', pergunta=pergunta, todos_departamentos=todos_departamentos)

@app.route('/admin/edit_question/<int:pergunta_id>', methods=['POST'])
def atualizar_pergunta(pergunta_id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('pagina_admin'))
    
    pergunta = Pergunta.query.get_or_404(pergunta_id)
    
    # Atualiza os campos básicos
    pergunta.tipo = request.form.get('tipo')
    pergunta.texto = request.form.get('texto')
    pergunta.data_liberacao = datetime.strptime(request.form.get('data_liberacao'), '%Y-%m-%d').date()

    # =========================================================
    # LÓGICA CORRIGIDA PARA ATUALIZAR A IMAGEM USANDO CLOUDINARY
    # =========================================================
    if 'imagem_pergunta' in request.files:
        file = request.files['imagem_pergunta']
        if file and file.filename != '' and allowed_file(file.filename):
            # (Opcional, mas boa prática: apaga a imagem antiga do Cloudinary)
            if pergunta.imagem_pergunta:
                # Extrai o 'public_id' da URL antiga para poder deletar
                public_id = pergunta.imagem_pergunta.split('/')[-1].split('.')[0]
                cloudinary.uploader.destroy(public_id)

            # Envia a NOVA imagem para o Cloudinary
            upload_result = cloudinary.uploader.upload(file, folder="perguntas_quiz")
            # Pega a URL segura que o Cloudinary devolveu
            imagem_url = upload_result.get('secure_url')
            # Atualiza a URL da pergunta no banco de dados
            pergunta.imagem_pergunta = imagem_url
    # =========================================================

    # Lógica para atualizar os setores (já estava correta)
    pergunta.departamentos.clear()
    if 'para_todos_setores' in request.form:
        pergunta.para_todos_setores = True
    else:
        pergunta.para_todos_setores = False
        departamento_ids = request.form.getlist('departamentos')
        if departamento_ids:
            departamentos_selecionados = Departamento.query.filter(Departamento.id.in_(departamento_ids)).all()
            pergunta.departamentos = departamentos_selecionados

    # Lógica para campos específicos do tipo (já estava correta)
    if pergunta.tipo in ['multipla_escolha', 'verdadeiro_falso']:
        pergunta.resposta_correta = request.form.get('resposta_correta')
        pergunta.tempo_limite = request.form.get('tempo_limite')
        if pergunta.tipo == 'multipla_escolha':
            pergunta.opcao_a, pergunta.opcao_b, pergunta.opcao_c, pergunta.opcao_d = request.form.get('opcao_a'), request.form.get('opcao_b'), request.form.get('opcao_c'), request.form.get('opcao_d')
        else:
            pergunta.opcao_a, pergunta.opcao_b, pergunta.opcao_c, pergunta.opcao_d = None, None, None, None
    else: # Discursiva
        pergunta.resposta_correta, pergunta.tempo_limite = None, None
        pergunta.opcao_a, pergunta.opcao_b, pergunta.opcao_c, pergunta.opcao_d = None, None, None, None
        
    db.session.commit()
    flash('Pergunta atualizada com sucesso!', 'success')
    return redirect(url_for('pagina_admin'))

@app.route('/admin/delete_question/<int:pergunta_id>', methods=['POST'])
def excluir_pergunta(pergunta_id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('pagina_admin'))
        
    pergunta = Pergunta.query.get_or_404(pergunta_id)
    
    # --- NOVA LÓGICA PARA APAGAR ARQUIVOS DO CLOUDINARY ---
    try:
        # 1. Apaga a imagem da pergunta, se existir
        if pergunta.imagem_pergunta:
            # Extrai o "public_id" da URL do Cloudinary
            public_id = pergunta.imagem_pergunta.split('/')[-1].split('.')[0]
            cloudinary.uploader.destroy(public_id)
            app.logger.info(f"Imagem {public_id} excluída do Cloudinary.")

        # 2. Busca todas as respostas da pergunta para apagar os anexos
        respostas_para_excluir = Resposta.query.filter_by(pergunta_id=pergunta.id).all()
        for resposta in respostas_para_excluir:
            if resposta.anexo_resposta:
                public_id_anexo = resposta.anexo_resposta.split('/')[-1].split('.')[0]
                # Usa 'destroy' com resource_type="raw" para arquivos como PDF, DOC
                cloudinary.uploader.destroy(public_id_anexo, resource_type="raw")
                app.logger.info(f"Anexo {public_id_anexo} excluído do Cloudinary.")

    except Exception as e:
        app.logger.error(f"Erro ao tentar excluir arquivos do Cloudinary: {e}")
        # Mesmo que falhe em apagar do Cloudinary, continua para apagar do banco
    # --- FIM DA NOVA LÓGICA ---

    # Apaga todas as respostas ligadas a esta pergunta no banco
    Resposta.query.filter_by(pergunta_id=pergunta.id).delete()
    
    # Apaga a pergunta do banco
    db.session.delete(pergunta)
    db.session.commit()
    
    flash('Pergunta e todas as suas respostas foram excluídas com sucesso.', 'success')
    return redirect(url_for('pagina_admin'))

@app.route('/admin/correcoes')
def pagina_correcoes():
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    usuarios_disponiveis = Usuario.query.order_by(Usuario.nome).all()
    usuario_selecionado_id = request.args.get('usuario_id', type=int)
    status_selecionado = request.args.get('status', 'pendente')
    query = Resposta.query.join(Pergunta).filter(Pergunta.tipo == 'discursiva')
    if status_selecionado != 'todos':
        query = query.filter(Resposta.status_correcao == status_selecionado)
    if usuario_selecionado_id:
        query = query.filter(Resposta.usuario_id == usuario_selecionado_id)
    respostas_filtradas = query.join(Usuario).order_by(Resposta.data_resposta.desc()).all()
    return render_template('correcoes.html', 
                           respostas=respostas_filtradas, 
                           usuarios_disponiveis=usuarios_disponiveis, 
                           usuario_selecionado_id=usuario_selecionado_id,
                           status_selecionado=status_selecionado)


@app.route('/admin/relatorios')
def pagina_relatorios():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('pagina_admin'))

    depto_selecionado_id = request.args.get('departamento_id', type=int)
    departamentos = Departamento.query.order_by(Departamento.nome).all()

    # Agora apenas chama a função auxiliar para obter os dados
    dados_relatorio = _gerar_dados_relatorio(depto_selecionado_id)

    return render_template('relatorios.html', 
                           relatorios=dados_relatorio, 
                           departamentos=departamentos, 
                           depto_selecionado_id=depto_selecionado_id)


@app.route('/admin/relatorios/exportar')
def exportar_relatorios():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('pagina_admin'))

    depto_selecionado_id = request.args.get('departamento_id', type=int)

    # 1. Reutiliza a mesma lógica de busca de dados
    dados_relatorio = _gerar_dados_relatorio(depto_selecionado_id)

    if not dados_relatorio:
        flash("Nenhum dado para exportar com os filtros selecionados.", "warning")
        return redirect(url_for('pagina_relatorios'))

    # 2. Converte os dados para um formato que o pandas entende
    df = pd.DataFrame(dados_relatorio)

    # 3. Renomeia e reordena as colunas para a planilha
    df = df.rename(columns={
        'nome': 'Colaborador',
        'setor': 'Setor',
        'total_respostas': 'Respostas Totais',
        'respostas_corretas': 'Respostas Corretas',
        'aproveitamento': 'Aproveitamento (%)',
        'pontuacao_total': 'Pontuação Total'
    })
    df['Aproveitamento (%)'] = df['Aproveitamento (%)'].map('{:.1f}%'.format)

    # 4. Cria o arquivo Excel em memória
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Relatorio de Desempenho')
    output.seek(0)

    # 5. Envia o arquivo para download
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='relatorio_desempenho_quiz.xlsx'
    )

@app.route('/admin/corrigir/<int:resposta_id>', methods=['POST'])
def corrigir_resposta(resposta_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('pagina_admin'))
        
    resposta = Resposta.query.get_or_404(resposta_id)
    
    novo_status = request.form.get('status')
    feedback_texto = request.form.get('feedback', '')
    
    # MUDANÇA: Adicionada a nova opção 'parcialmente_correto'
    if novo_status in ['correto', 'incorreto', 'parcialmente_correto']:
        resposta.status_correcao = novo_status
        resposta.feedback_admin = feedback_texto
        
        if novo_status == 'correto':
            resposta.pontos = 100
        elif novo_status == 'parcialmente_correto':
            resposta.pontos = 50 # Pontuação intermediária
        else: # Incorreto
            resposta.pontos = 0
            
        db.session.commit()
        flash('Resposta avaliada com sucesso!', 'success')
    else:
        flash('Ação de correção inválida.', 'danger')
        
    return redirect(url_for('pagina_correcoes'))

@app.route('/admin/analytics')
def pagina_analytics():
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    usuarios_disponiveis = Usuario.query.order_by(Usuario.nome).all()
    usuario_selecionado_id = request.args.get('usuario_id', type=int)
    base_query = Resposta.query.join(Pergunta).filter(Pergunta.tipo != 'discursiva')
    if usuario_selecionado_id:
        base_query = base_query.filter(Resposta.usuario_id == usuario_selecionado_id)
    todas_as_respostas_objetivas = base_query.all()
    stats_perguntas_raw = defaultdict(lambda: {'total': 0, 'erros': 0})
    for resposta in todas_as_respostas_objetivas:
        stats_perguntas_raw[resposta.pergunta_id]['total'] += 1
        if resposta.pontos == 0:
            stats_perguntas_raw[resposta.pergunta_id]['erros'] += 1
    stats_perguntas = []
    for pergunta_id, data in stats_perguntas_raw.items():
        pergunta = Pergunta.query.get(pergunta_id)
        if pergunta:
            percentual = (data['erros'] / data['total']) * 100 if data['total'] > 0 else 0
            stats_perguntas.append({'texto': pergunta.texto, 'total': data['total'], 'erros': data['erros'], 'percentual': percentual})
    stats_perguntas.sort(key=lambda x: x['percentual'], reverse=True)
    respostas_erradas_query = Resposta.query.join(Pergunta).filter(Resposta.pontos == 0, Pergunta.tipo != 'discursiva')
    if usuario_selecionado_id:
        respostas_erradas_query = respostas_erradas_query.filter(Resposta.usuario_id == usuario_selecionado_id)
    respostas_erradas = respostas_erradas_query.join(Usuario).join(Departamento).order_by(Departamento.nome, Usuario.nome).all()
    erros_por_setor = defaultdict(lambda: defaultdict(list))
    for r in respostas_erradas:
        setor_nome, usuario_nome = r.usuario.departamento.nome, r.usuario.nome
        erros_por_setor[setor_nome][usuario_nome].append({
            'pergunta_texto': r.pergunta.texto, 'data_liberacao': r.pergunta.data_liberacao.strftime('%d/%m/%Y'),
            'resposta_dada': r.resposta_dada, 'texto_resposta_dada': get_texto_da_opcao(r.pergunta, r.resposta_dada),
            'resposta_correta': r.pergunta.resposta_correta, 'texto_resposta_correta': get_texto_da_opcao(r.pergunta, r.pergunta.resposta_correta)
        })
    return render_template('analytics.html', 
                           stats_perguntas=stats_perguntas, erros_por_setor=erros_por_setor,
                           usuarios_disponiveis=usuarios_disponiveis, usuario_selecionado_id=usuario_selecionado_id)

@app.route('/admin/upload_planilha', methods=['POST'])
def upload_planilha():
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    arquivo = request.files.get('arquivo_planilha')
    if not arquivo or not (arquivo.filename.lower().endswith('.xls') or arquivo.filename.lower().endswith('.xlsx')):
        flash('Arquivo inválido ou não selecionado. Envie uma planilha .xls ou .xlsx.', 'danger')
        return redirect(url_for('pagina_admin'))
    try:
        df = pd.read_excel(arquivo)
        df = df.fillna('')
        if 'data_liberacao' in df.columns:
            df['data_liberacao'] = pd.to_datetime(df['data_liberacao'], errors='coerce').dt.strftime('%d/%m/%Y').fillna('')
        for col in df.columns:
            if col != 'data_liberacao':
                df[col] = df[col].astype(str).str.replace(r'\.0$', '', regex=True)
        headers = df.columns.tolist()
        dados_da_planilha = df.to_dict(orient='records')
        session['csv_headers'] = headers
        validated_data = []
        has_valid_rows = False
        for row in dados_da_planilha:
            is_valid, errors = validar_linha(row)
            if is_valid: has_valid_rows = True
            validated_data.append({'data': row, 'is_valid': is_valid, 'errors': errors})
        session['csv_data'] = validated_data
        session['has_valid_rows'] = has_valid_rows
        return redirect(url_for('preview_csv'))
    except Exception as e:
        app.logger.error(f"Erro ao ler a planilha Excel: {e}")
        flash(f"Ocorreu um erro inesperado ao processar a planilha: {e}", "danger")
        return redirect(url_for('pagina_admin'))

@app.route('/admin/preview_csv')
def preview_csv():
    if not session.get('admin_logged_in'): return redirect(url_for('pagina_admin'))
    validated_data = session.get('csv_data', [])
    has_valid_rows = session.get('has_valid_rows', False)
    headers = session.get('csv_headers', [])
    return render_template('preview_csv.html', data=validated_data, has_valid_rows=has_valid_rows, headers=headers)

# Em app.py

@app.route('/admin/processar_edicao_csv', methods=['POST'])
def processar_edicao_csv():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('pagina_admin'))

    # 1. Reconstrói os dados da planilha a partir do formulário editado
    rows_data = defaultdict(dict)
    for key, value in request.form.items():
        if key.startswith('row-'):
            parts = key.split('-', 2)
            row_index = int(parts[1])
            col_name = parts[2]
            rows_data[row_index][col_name] = value

    success_count = 0
    error_count = 0
    perguntas_para_notificar = []
    
    # 2. Loop através das linhas corrigidas para salvar no banco
    for row_index in sorted(rows_data.keys()):
        row = rows_data[row_index]
        is_valid, errors = validar_linha(row) # Revalida a linha
        
        if is_valid:
            try:
                data_obj = datetime.strptime(row['data_liberacao'], '%d/%m/%Y').date()
                nova_pergunta = Pergunta(
                    tipo=row['tipo'], texto=row['texto'],
                    opcao_a=row.get('opcao_a') or None, opcao_b=row.get('opcao_b') or None,
                    opcao_c=row.get('opcao_c') or None, opcao_d=row.get('opcao_d') or None,
                    resposta_correta=row.get('resposta_correta') or None, 
                    data_liberacao=data_obj,
                    tempo_limite=int(float(row['tempo_limite'])) if row.get('tempo_limite') else None
                )
                db.session.add(nova_pergunta)
                
                # if row.get('enviar_notificacao', '').lower() == 'sim':
                #     perguntas_para_notificar.append(nova_pergunta)
                
                success_count += 1
            except Exception as e:
                db.session.rollback()
                error_count += 1
                app.logger.error(f"Erro ao salvar linha {row_index} (após correção): {e} | Dados: {row}")
        else:
            error_count += 1
            app.logger.error(f"Linha {row_index} ainda inválida após edição: {errors}")

    db.session.commit()
    
    # for pergunta in perguntas_para_notificar:
    #     disparar_notificacao_nova_pergunta(pergunta)
        
    session.pop('csv_data', None)
    session.pop('has_valid_rows', None)
    session.pop('csv_headers', None)
    
    if error_count > 0:
        flash(f'Importação parcial: {success_count} perguntas salvas. {error_count} linhas continham erros e foram ignoradas.', 'warning')
    else:
        flash(f'Importação concluída! {success_count} perguntas foram importadas com sucesso!', 'success')
        
    return redirect(url_for('pagina_admin'))

# Em app.py, no final do arquivo

# --- ROTA DE SERVIÇO PARA INICIALIZAR/RESETAR O BANCO DE DADOS LOCAL ---
@app.route('/_init_db/<secret_key>')
def init_db(secret_key):
    # Use uma chave diferente da senha de admin para mais segurança
    if secret_key != 'resetar-banco-123':
        return "Chave secreta inválida.", 403
    try:
        app.logger.info("Iniciando a reinicialização do banco de dados...")
        db.drop_all()
        db.create_all()
        app.logger.info("Tabelas criadas. Inserindo dados iniciais...")
        
        # Dados iniciais para usuários e setores
        dados_iniciais = {
            "Suporte": [
                {'nome': 'Ana Oliveira', 'codigo_acesso': '1234', 'email': 'ana.oliveira@empresa.com'},
                {'nome': 'Bruno Costa', 'codigo_acesso': '5678', 'email': 'bruno.costa@empresa.com'}
            ],
            "Vendas": [
                {'nome': 'Carlos Dias', 'codigo_acesso': '9012', 'email': 'carlos.dias@empresa.com'},
                {'nome': 'Daniela Lima', 'codigo_acesso': '3456', 'email': 'daniela.lima@empresa.com'}
            ]
        }
        
        for nome_depto, lista_usuarios in dados_iniciais.items():
            novo_depto = Departamento(nome=nome_depto)
            db.session.add(novo_depto)
            for user_data in lista_usuarios:
                novo_usuario = Usuario(
                    nome=user_data['nome'], 
                    codigo_acesso=user_data['codigo_acesso'],
                    email=user_data['email'],
                    departamento=novo_depto
                )
                db.session.add(novo_usuario)
        
        db.session.commit()
        app.logger.info("Banco de dados inicializado com sucesso!")
        return "<h1>Banco de dados inicializado com sucesso!</h1>"
    except Exception as e:
        app.logger.error(f"Ocorreu um erro na inicialização do banco de dados: {e}")
        return f"<h1>Ocorreu um erro:</h1><p>{e}</p>", 500

# Esta deve ser a última parte do seu arquivo
if __name__ == '__main__':
    app.run(debug=True)
