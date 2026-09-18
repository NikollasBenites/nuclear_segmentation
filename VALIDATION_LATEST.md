# Validação com versões estáveis recentes — 18/09/2026

Aplicativo: Nuclear Segmentation 0.2.0.
Ambiente executado: Linux x86_64, Python 3.12.14, Qt/PyQt6 em modo offscreen.

## Versões instaladas e testadas

| Biblioteca | Versão |
|---|---|
| Napari | 0.9.1 |
| NumPy | 2.5.3 |
| SciPy | 1.18.1 |
| pandas | 3.0.6 |
| scikit-image | 0.26.0 |
| tifffile | 2026.9.15 |
| Matplotlib | 3.11.2 |
| magicgui | 0.10.2 |
| QtPy | 2.4.3 |
| PyQt6 | 6.11.0 |

As versões foram consultadas no índice oficial PyPI e instaladas com pip sem
permitir pré-lançamentos. A lista acima corresponde às versões efetivamente
instaladas, não apenas a intervalos declarados no pyproject.toml.
Fontes: https://pypi.org/project/napari/ e as páginas PyPI de cada biblioteca.

NumPy 2.5.3, SciPy 1.18.1 e tifffile 2026.9.15 requerem Python >=3.12.
Por isso esta distribuição moderna exige Python >=3.12; o ambiente 3.11 do
usuário deve ser preservado e outro ambiente criado. Python 3.13+ não foi testado.

## Resultado

**9 testes passaram, sem falhas, em 5,38 segundos.**
`python -m pip check` retornou **No broken requirements found.**

1. Densidade, conversão por cubo de 100 µm, correção nominal de 60 µm,
   independência entre espessura completa e guarda, volume vazio e revisão pendente.
2. Reconstrução de ROI e validação da calibração.
3. Descoberta da ROI salva mais recentemente.
4. TIFF multicanal sintético → resume → medidas → shells → painel de filtros →
   revisão da ROI → exportação TIFF/CSV/JSON/PDF → reabertura density_only.
   Foram testados o pincel real de Labels (invalidação da revisão), mudanças no
   filtro de esfericidade, valores de densidade e bloqueio de exportação inválida.
5. Inicialização e configuração dos controles Qt.
6. Encaminhamento dos parâmetros para segmentação nova com modelo simulado.
7. Execução de retomada pela thread da interface.
8. Igualdade dos notebooks sincronizados e equivalência da fórmula de densidade M3.
9. Ponto de entrada main([]) com Viewer real do Napari, verificando o painel
   instalado. Apenas o loop bloqueante napari.run é substituído nesse teste.

O teste integrado também verifica unidades micrométricas em todas as camadas e
visibilidade da barra de escala pela API atual.

## Ajustes necessários

A primeira execução dos nove testes já passou, mas os avisos revelaram que
`viewer.scale_bar.unit` não tem mais efeito no Napari 0.9.
A versão 0.2.0 define `layer.units` nas camadas e usa
`viewer.canvas.overlays.scale_bar.visible`. Isso corrige a exibição das unidades;
os cálculos de volume/densidade já utilizavam o voxel spacing explicitamente.
Não foi necessário mudar as regras científicas de segmentação/filtragem.

Os intervalos das dependências principais agora têm apenas mínimos, estabelecidos
nas versões testadas acima. O arquivo constraints-tested-py312.txt fixa essa
combinação para instalação reproduzível das dependências principais. Isso não
representa garantia para qualquer atualização futura. O JSON de ambiente registra
as versões transitivas completas observadas no teste Linux.

Restaram 70 avisos de depreciação internos a Pydantic e scikit-image/NumPy;
nenhum causou falha. Eles não foram silenciados no relatório de execução.
Os avisos emitidos pelo próprio app sobre a barra de escala foram corrigidos.

## O que esta validação não demonstra

- Não houve teste em macOS Apple Silicon, Windows, CUDA ou MPS.
- A integração usa camadas reais do Napari e widgets Qt reais, mas o canvas 3D
  OpenGL não foi validado neste ambiente sem display gráfico funcional.
- Não foi realizada inferência real com Cellpose, pesos cpsam_v2 ou TIFF experimental.
- Os testes não validam acurácia biológica, identidade neuronal nem ausência de
  viés estereológico. Eles verificam execução e consistência do fluxo implementado.
- O extra opcional de cache permanece na API Zarr 2; não foi migrado para Zarr 3.

Uma resolução adicional com pip --dry-run para [gui,segmentation] teve sucesso
em Linux, selecionando Cellpose 4.2.1.1, PyTorch 2.14.0 e torchvision 0.29.0.
Isso verifica apenas compatibilidade declarada das dependências: esses pacotes
não foram instalados nem sua inferência executada nesta validação.

## Repetir os testes

No ambiente de teste, na pasta extraída:

```sh
python -m pip install ".[gui,test]" -c constraints-tested-py312.txt
python -m pytest -q
python -m pip check
```

O primeiro teste com dados reais recomendado é abrir uma análise anterior e
comparar contagem, densidade e exportação com os mesmos filtros, limites Z e ROI.
