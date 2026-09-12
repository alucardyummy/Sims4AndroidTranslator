<div align="center">
  <img src="img/icon-pf.webp" width="80" />

  ### Sims4AndroidTranslator

  Ferramenta web para traduzir, mesclar e criar mods (`.package`) do The Sims 4 direto pelo celular Android, sem precisar de PC.

  **[sims4androidtranslator.vercel.app](https://sims4androidtranslator.vercel.app)**

</div>

---

### Como usar

1. Acesse o site e toque em **IMPORTAR**, selecionando o `.package` do mod
2. Escolha o idioma de destino
3. Traduza as strings (manual ou automático)
4. Dê um nome ao arquivo de saída e salve
5. Baixe o `.package` traduzido e jogue direto na pasta `Mods`

---

### Traduzir mods

O core do site: lê as STBLs (tabelas de string) do `.package`, permite editar cada texto e gera um novo pacote com o idioma de destino, sem alterar a instância original.

- **Manual** — toque no ícone de traduzir ao lado de qualquer string (ou em várias selecionadas de uma vez) pra abrir o menu de modelos e escolher qual usar
- **Em massa** — botão "Traduzir tudo" roda a tradução automática em todas as strings do arquivo de uma vez, usando o modelo selecionado
- **Atenção a placeholders** — sempre revise se termos como `{SimFirstName}`, `{SimPronounSubjective}`, `{SimPronounObjective}` etc. continuam intactos após a tradução automática

### Modelos de tradução disponíveis

O site possui alguns modelos diversificados para atender a diferentes nichos de Mods. Se um falhar ou estiver fora do ar, tenta o próximo sozinho.

> Alguns desses modelos rodam localmente, por isso, nem sempre estarão funcionando. Os demais usuários podem escolhê-los, mas se estiverem offline a tradução passa direto pra próxima opção da lista.

### Salvar progresso e continuar depois

- Progresso pode ser salvo a qualquer momento e retomado depois pelo menu de projetos salvos
- Funciona sem conta (sessão de convidado, guardada por cookie) ou vinculado a uma conta (username/senha ou login com Google) — os saves de convidado migram automaticamente pra conta ao fazer login
- **Sempre salve antes de sair**: o rascunho automático da home só funciona se o projeto já tiver nome

### Importar traduções existentes

Duas formas de reaproveitar trabalho já feito:

- **De um save salvo** — reimporta uma tradução feita no site em outro `.package`, desde que as keys coincidam
- **De um `.package` já traduzido** — extrai as strings de um pacote que já tem a tradução embutida (detecta o idioma certo automaticamente quando o pacote tem mais de uma tabela de idioma)

### Mesclar `.packages`

Junta vários arquivos `.package` em um só — útil pra combinar traduções de um mesmo mod num único arquivo.

- **Simples** — mescla direta
- **Inteligente** — grava um manifesto interno com a origem de cada recurso, permitindo desmesclar o arquivo de volta nos originais depois

### Contas

- Cadastro com usuário/senha (recuperação por pergunta de segurança) ou login com Google
- Perfil com nome de usuário e foto customizáveis
- Sessão de convidado dura 30 dias e migra pra conta automaticamente no login

---

### Stack

Flask · PostgreSQL (via Supabase) · Google OAuth · Vercel

---

### Créditos

Projeto original: **[The Sims 4 Translator](https://github.com/voky1/sims4-translator)**, por [Voky1](https://github.com/voky1) — este é um remake mobile-first, reconstruído do zero para rodar direto no navegador do Android.

<div align="center">
  <img src="img/sillyblumbob.webp" width="60" /><br/>
  <sub>Remake mobile: <b>AlucardYummy</b></sub>
</div>
