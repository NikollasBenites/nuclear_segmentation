# Validação v0.2.1

Adicionados CELLPOSE_RESAMPLE, CELLPOSE_RESCALE e CELLPOSE_NORMALIZE.

Executado nesta atualização: 4 testes unittest sem dependências GUI, cobrindo
configurações inválidas, padrões de presets antigos, JSON roundtrip, construção
real do dicionário eval_parameters por AST, exportação e restauração (inclusive
análises antigas). Compilação sintática de todos os módulos concluída.

Os testes de integração existentes foram ampliados para verificar a passagem
de valores não padrão ao modelo e roundtrip da interface. Não foram executados
novamente nesta sessão: o ambiente temporário com Qt/Cellpose não está disponível.
Não foi feita inferência real do Cellpose nem teste gráfico no macOS nesta atualização.
