# Validação — 17/09/2026

Testes automatizados em Linux, Python 3.12, Napari 0.6.6 e PyQt6:

- Conversão µm³/mm³/(100 µm)³ e correção nominal para 60 µm.
- Espessura derivada do intervalo Z inclusivo, independente da guarda.
- Guardas none/first/last/both, guarda zero e volume efetivo vazio.
- Bloqueio de densidade antes da revisão da ROI e após edição.
- Reconstrução de ROI com buraco interno e busca da ROI salva mais recente.
- TIFF sintético multicanal com metadados ImageJ e máscaras com IDs não contíguos.
- Resume sem importar/executar Cellpose: morfologia, intensidade, shells, filtros.
- Exportação TIFF/CSV/JSON/figuras/PDF e reabertura em density_only.
- Estado de filtro inválido não permite exportar resultados antigos.
- Interface Qt: round-trip de parâmetros/presets e execução via QThread.
- Nova segmentação: chamada ao modelo simulada para conferir parâmetros e
  dispositivo; não foi executada inferência Cellpose real.
- Igualdade dos dois notebooks e equivalência da função de densidade com M3.

A integração usa o modelo de camadas real do Napari e widgets Qt reais sem um
canvas OpenGL. A janela de configuração foi renderizada e inspecionada. O
ambiente de teste não dispõe de contexto OpenGL funcional para validar o canvas
3D; a renderização volumétrica deve ser conferida no computador de destino.

Não testado em hardware Mac/Windows, GPU CUDA/MPS ou em seu TIFF experimental.
Não é uma validação de acurácia biológica, classificação neuronal ou segmentação.
O primeiro teste recomendado é abrir uma análise anterior pequena e comparar os
mesmos filtros, limites Z e ROI com o notebook, antes de segmentar novas imagens.

Para repetir: instalar o extra [test] e executar `python -m pytest -q` na pasta
extraída. Em servidor sem monitor, use QT_QPA_PLATFORM=offscreen e MPLBACKEND=Agg.

Resultado desta distribuição: **8 testes passaram**. Wheel construído e instalado
em um diretório isolado; entrada CLI, configuração e os 12 scripts de etapas
foram encontrados no pacote instalado.

## Correção 0.1.1
Removida atribuição de atributo não permitido ao Viewer Pydantic. Teste de
regressão executa main([]) com Viewer real e verifica a criação do painel;
apenas o loop bloqueante napari.run é substituído no teste. Teste passou.
Os testes da versão anterior não cobriam a função de entrada completa.
