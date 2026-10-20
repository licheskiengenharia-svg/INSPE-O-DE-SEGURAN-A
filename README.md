# Inspeção de Segurança

Sistema web de inspeções de segurança do trabalho organizado por setor (Usinagem, Fusão, Macharia, Savelli, Cura Frio, Rebarbação e jateamento, Pintura e outros). Cada setor recebe as seções de perguntas que se aplicam a ele (EPI, elétrica, máquinas, químicos, frentes de trabalho, fornos, cargas, ambiente, incêndio, áreas comuns e 5S).

- Tela "Setores e perguntas" para criar setores e editar seções e perguntas
- Registro de inspeções com itens Conforme / Não conforme / N/A
- Não conformidades com ação corretiva, responsável e prazo
- Painel de ações: índice de conformidade, ações concluídas, em aberto e atrasadas, gráficos por mês, por setor, por norma e por TST
- Dados do TST em cada inspeção (nome, registro, data, horário e turno)
- Histórico com busca, relatório em Excel com dashboard (gerado ao final de cada inspeção) e exportação CSV

As inspeções ficam salvas no navegador de quem usa o site (localStorage). Use **Exportar CSV** para guardar uma cópia.

A pasta `sketchup/` traz um galpão modelo (30 × 20 m) com os elementos de segurança: `galpao-seguranca.dae` (Arquivo > Importar no SketchUp) e `galpao-seguranca.rb` (Console Ruby: `load "caminho/galpao-seguranca.rb"`).
