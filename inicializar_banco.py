from app import app, db, Usuario, Departamento

# ESTRUTURA DOS SETORES E USUÁRIOS
dados_iniciais = {
    "Suporte": [
        # MUDANÇA: Adicionado o campo 'email' para cada usuário
        {'nome': 'Jenyffer', 'codigo_acesso': '1234', 'email': 'jenycds8@gmail.com'},
        {'nome': 'Bruno Costa', 'codigo_acesso': '5678', 'email': 'bruno.costa@empresa.com'},
    ],
    "Vendas": [
        {'nome': 'Carlos Dias', 'codigo_acesso': '9012', 'email': 'carlos.dias@empresa.com'},
        {'nome': 'Daniela Lima', 'codigo_acesso': '3456', 'email': 'daniela.lima@empresa.com'},
    ],
    "CSM": [
        {'nome': 'Eduardo Martins', 'codigo_acesso': '1111', 'email': 'eduardo.martins@empresa.com'},
        {'nome': 'Fernanda Souza', 'codigo_acesso': '2222', 'email': 'fernanda.souza@empresa.com'},
    ],
    "ISM": [
        {'nome': 'Gustavo Pereira', 'codigo_acesso': '3333', 'email': 'gustavo.pereira@empresa.com'},
    ]
}

with app.app_context():
    print("Apagando e recriando o banco de dados...")
    db.drop_all() # Garante que o banco de dados antigo seja apagado
    db.create_all()

    print("Inserindo departamentos e usuários...")
    for nome_depto, lista_usuarios in dados_iniciais.items():
        # Cria o departamento
        novo_depto = Departamento(nome=nome_depto)
        db.session.add(novo_depto)
        
        # Cria os usuários e já os associa ao departamento
        for user_data in lista_usuarios:
            novo_usuario = Usuario(
                nome=user_data['nome'], 
                codigo_acesso=user_data['codigo_acesso'],
                email=user_data['email'], # Adicionamos o e-mail aqui
                departamento=novo_depto
            )
            db.session.add(novo_usuario)
    
    db.session.commit()
    print("Dados iniciais inseridos com sucesso!")

    print("Banco de dados pronto!")