<div align="center">
  <img src="img/icon-pf.webp" width="80" />

---

✦ O **Sims4AndroidTranslator** é uma ferramenta web que permite traduzir arquivos `.package` do The Sims 4 diretamente pelo celular Android, sem precisar de um PC.

Ele é um remake mobile-first baseado no projeto [Voky1](https://github.com/voky1), reconstruído com Flask + Kivy e hospedado via Vercel.

---

## ✦ Como usar

1. Acesse **[droidtranslator.vercel.app](https://droidtranslator.vercel.app)**
2. Toque em **IMPORTAR** e selecione o arquivo `.package` do seu celular
3. Escolha o idioma de destino
4. Faça sua tradução
5. Baixe o arquivo traduzido
6. Adicione o arquivo traduzido diretamente na sua pasta Mods
<p align="center">Fácil né? :3</p>

---

## ✦ Tecnologias

| Camada | Tecnologia |
|---|---|
| Backend | Python · Flask |
| Empacotamento Android | Buildozer · Kivy |
| Frontend | HTML · Jinja2 |
| Deploy | Vercel |

---

## ✦ Estrutura do projeto

```
├── app.py           • Servidor Flask principal
├── translator.py    • Lógica de tradução dos .package
├── db.py            • Banco de dados / autenticação
├── main.py          • Entry point Kivy (Android)
├── templates/       • HTML das páginas
├── utils/           • Funções auxiliares
├── java/org/kivy/   • Bridge Android nativa
└── buildozer.spec   • Config de build Android
```

---

## ✦ Rodando localmente

```bash
git clone https://github.com/alucardyummy/Sims4AndroidTranslator
cd Sims4AndroidTranslator
pip install -r requirements.txt
python app.py
```

---

## ✦ Créditos

- Projeto original: **[Voky1](https://github.com/voky1)**

---

## Meus outros projetos

- Emular TS4 no Android (guia): **[Sims4AndroidBR](https://sims4androidbr.vercel.app)**

---

<div align="center">
  <img src="img/sillyblumbob.webp" width="60" /><br/>
  <sub>Feito com ♡ e muita gambiarra · MIT · 2026</sub>
  <sub>Remake mobile: <b>AlucardYummy<b/></sub>
</div>
