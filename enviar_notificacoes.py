# Em enviar_notificacoes.py

from app import app, db, mail, Usuario, Pergunta
from flask_mail import Message
from datetime import date

def enviar_email_notificacao():
    # 'with app.app_context()' é crucial para permitir que o script acesse o banco de dados
    with app.app_context():
        print("Iniciando verificação de novas perguntas...")
        
        # 1. Encontra as perguntas liberadas hoje
        hoje = date.today()
        perguntas_de_hoje = Pergunta.query.filter_by(data_liberacao=hoje).all()
        
        if not perguntas_de_hoje:
            print("Nenhuma pergunta nova para hoje. Encerrando.")
            return

        print(f"Encontradas {len(perguntas_de_hoje)} perguntas novas. Buscando usuários...")
        
        # 2. Busca todos os usuários que têm um e-mail cadastrado
        usuarios = Usuario.query.filter(Usuario.email.isnot(None)).all()
        
        if not usuarios:
            print("Nenhum usuário com e-mail cadastrado. Encerrando.")
            return

        # 3. Envia um e-mail para cada usuário
        # Usamos 'with mail.connect()' para otimizar o envio de múltiplos e-mails
        with mail.connect() as conn:
            for usuario in usuarios:
                try:
                    subject = "Novas perguntas disponíveis no Quiz Produtivo!"
                    body = (
                        f"Olá, {usuario.nome}!\n\n"
                        f"Temos novas perguntas de conhecimento liberadas hoje para você responder.\n\n"
                        f"Acesse agora e teste seus conhecimentos!\n\n"
                        f"Atenciosamente,\nEquipe Quiz Produtivo"
                    )
                    
                    msg = Message(subject=subject, recipients=[usuario.email], body=body)
                    conn.send(msg)
                    print(f"E-mail enviado com sucesso para {usuario.email}")
                except Exception as e:
                    print(f"Falha ao enviar e-mail para {usuario.email}: {e}")

        print("Processo de notificação concluído.")

# Permite que o script seja executado diretamente pelo terminal
if __name__ == '__main__':
    enviar_email_notificacao()