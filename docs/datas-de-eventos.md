# Datas de eventos e atrasos

O cadastro e a edição aceitam `DD/MM/AAAA`, `DD/MM/AAAA HH:MM` e
`DD/MM/AAAA HH:MM:SS`. O horário é opcional e usa o relógio local, em 24 horas;
para compatibilidade, componentes numéricos não preenchidos com zero e espaços
externos são aceitos. O ano tem quatro dígitos. Não se adota fuso/offset no
texto armazenado. Datas impossíveis, horários fora da faixa e períodos
invertidos não são gravados. Limites iguais são permitidos.

Sem horário, o início considera 00:00 e o término inclui o dia indicado por
inteiro. Um término sem horário vira atraso só no dia seguinte. Com horário, o
atraso começa depois do horário exato; sem segundos explícitos, o corte é
`:00`. A checagem exige evento agendado e equipamento ainda pendente; o mesmo
período nunca altera o estado de uma saída, manutenção ou devolução.

Dados antigos não sofrem conversão ou reparo automático. Um período inválido
(inclusive invertido) aparece como **Datas a revisar** na lista e no histórico,
e o status originalmente gravado permanece consultável. No painel, os
agendados com período inválido ficam fora da contagem de ativos com período
sadio e recebem um aviso. Na checagem de pendências, períodos inválidos ficam
separados dos atrasos confirmados. Corrigir um período implica uma edição
explícita onde a política já a permite; esta alteração não libera edição de
eventos concluídos nem muda o fluxo existente de equipamentos e notas.

Os relatórios de eventos sinalizam períodos inválidos sem alterar o texto salvo.
O critério existente de equipamento pendente não foi alterado; inconsistências
nos movimentos continuam tratadas separadamente pelo M1.
