# Sincronização M3 → Windows

Referência: general_nuclei_segmentation_m3_v5_5_live_perinuclear_shells.ipynb
recebido em 17/09/2026. A versão Windows recebida ainda não continha todas as
alterações feitas no Mac.

Aplicado igualmente aos dois notebooks entregues:

- Configuração padrão RUN_MODE="resume" (Windows usava "segment").
- Guarda inicial de 0 µm (Windows usava 2,5 µm), modo "first" preservado.
- Espessura nominal de 60 µm e espessura atual derivada dos limites Z inclusivos.
- Densidade medida e corrigida por cubo (100 µm)³.
- Volumes em µm³ e textos do painel exatamente como personalizados na versão M3.
- Formatação multilinha nos painéis normal e density_only.
- QC adicional de intensidade DAPI por Z.
- Integração Qt/Jupyter em ambos; seleção CUDA/MPS/CPU já era idêntica.
- Outputs e execution_count antigos removidos para não apresentar resultados
  de uma amostra como se fossem resultados da próxima execução.

Pequenos ajustes adicionais nos dois notebooks:

- Espessura nominal gravada/restaurada em analysis_config.json.
- Campo used_gpu também reconhece MPS; dispositivo registrado explicitamente.

Os códigos e configurações dos dois notebooks sincronizados são idênticos.
As diferenças de hardware são tratadas automaticamente no código existente.

No aplicativo, além da extração para scripts:

- Interface de arquivos, parâmetros, canais, presets, atividade e exportação.
- Processamento de imagem/medidas em thread separada; camadas criadas na thread GUI.
- Retomada/density_only dispensam importar Cellpose/PyTorch.
- ROI mais recente descoberta entre a pasta principal e seus density_addenda.
- Exportações completas em nova pasta com timestamp, evitando sobreposição.
- Verificação de transformações indevidas da ROI e bloqueio de densidade obsoleta.
- QC executado por botão; scripts independentes de Jupyter/IPython.
- Ainda preserva a restrição científica do notebook: DAPI e marcador perinuclear
  habilitado no fluxo completo. Não implementa classificação neuronal automática.
