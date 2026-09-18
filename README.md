# Nuclear Segmentation 0.2.0

Aplicativo desktop em Python + Napari baseado no notebook M3 v5.5 de Nikollas.
Mesmas configurações e regras científicas em macOS Apple Silicon e Windows.
Não exige Jupyter nem execução de células. Esta primeira versão é um pacote
instalável, não um executável autônomo `.exe`/`.app` com Python embutido.

## Instalação desta versão (0.2.0)

Esta distribuição foi testada com as versões estáveis mais recentes das
bibliotecas principais disponíveis em 18/09/2026. Exige **Python 3.12 ou superior**;
o ambiente validado usou Python 3.12.14. Python 3.13+ não foi testado.
NumPy 2.5.3, SciPy 1.18.1 e tifffile 2026.9.15 exigem Python >=3.12.
Não instale esta distribuição no seu ambiente nuclearapp com Python 3.11.
Mantenha esse ambiente e crie outro:

```sh
conda create -n nuclearapp_latest python=3.12
conda activate nuclearapp_latest
cd "CAMINHO/nuclear_segmentation_app"
python -m pip install ".[gui]" -c constraints-tested-py312.txt
nuclear-segmentation --version
nuclear-segmentation
```

O comando de versão deve mostrar **0.2.0**. O arquivo de constraints fixa as
versões principais testadas. O pyproject.toml usa apenas limites mínimos para
as dependências principais; atualizações futuras continuam exigindo testes.

O pacote básico permite abrir análises existentes, ajustar filtros, calcular
medidas/densidade e exportar, sem instalar Cellpose/PyTorch. Para novas segmentações,
instale primeiro o PyTorch adequado ao hardware seguindo
https://pytorch.org/get-started/locally/ e então o extra de segmentação:

```sh
python -m pip install ".[segmentation]" -c constraints-tested-py312.txt
```

A escolha CUDA/MPS/CPU depende do hardware e da distribuição de PyTorch instalada.
Não substitua um PyTorch CUDA existente por uma instalação CPU sem intenção.
A inferência real de Cellpose e os backends GPU não foram executados nesta validação;
veja VALIDATION_LATEST.md. O modelo cpsam_v2 deve existir na versão de Cellpose
instalada ou ser indicado por caminho local. A primeira execução pode baixar pesos.

Para cache opcional: `python -m pip install ".[zarr]"`.
O cache ainda usa a API Zarr 2; os limites desse extra são intencionais e
não foram removidos. Cellpose/PyTorch e o cache não fazem parte da tabela de
versões principais testadas desta atualização.

Os iniciadores em `launchers` ativam nuclearapp_latest. Como alternativa ao comando
principal, execute `python -m nuclear_segmentation`.

## Fluxo de uso

1. **Files:** escolha o modo e o TIFF recortado original.
   - **New segmentation:** executa o Cellpose, mede núcleos e regiões perinucleares.
   - **Resume analysis / adjust filters:** abre máscaras anteriores sem Cellpose;
     recalcula morfologia, intensidades e regiões perinucleares, como o notebook.
   - **Previous analysis: density only:** usa os núcleos previamente retidos;
     não repete segmentação nem medidas. O painel deste modo não oferece filtros.
2. Em resume/density-only selecione a pasta com `analysis_config.json` e
   `cellpose_raw_masks.tif`; não selecione apenas a pasta `density_addendum`.
3. **Channels/Perinuclear markers:** para segmentação nova, confirme canais,
   índices zero-based, limites e marcadores. Preservamos a restrição do notebook:
   canal de segmentação denominado DAPI e ao menos um marcador perinuclear ativo.
4. **Parameters:** confirme modelo, batch size, suavização, espessura nominal,
   ROI e calibração. Em retomadas, a configuração anterior restaura canais,
   filtros, parâmetros de modelo, calibração e espessura nominal (quando salva).
5. Clique **Start analysis**. O painel Activity mostra as etapas e o dispositivo.
   O processamento pesado usa uma thread; a segmentação não tem porcentagem
   granular nem cancelamento imediato nesta versão. Aguarde antes de fechar.
6. Revise/edite a ROI em 2D, passando pelos planos Z. Use pincel/borracha, não
   a ferramenta de transformação da camada. Aceite a ROI para liberar densidade.
7. Ajuste os filtros, first/last tissue Z e guardas no painel à direita.
8. Use **Export results**, **QC plots** ou **Save ROI…**. Para somente densidade,
   também existe **Save density addendum** no painel específico.
9. Para outra amostra, escolha os arquivos e Start novamente; salve suas alterações
   antes de substituir a visualização.

**Configurações reutilizáveis:** Save preset/Load preset gravam JSON. Advanced
settings expõe os parâmetros menos frequentes sem editar arquivos Python.
Esses presets são diferentes do `analysis_config.json` de uma análise exportada.
Não carregue este último como um preset; selecione sua pasta em Previous analysis.

## ROI, calibração e densidade

- A ROI é inferida do sinal não zero em qualquer canal original, com preenchimento
  de buracos XY. É uma estimativa do recorte; revise as bordas e todas as fatias.
- Caminho de ROI explícito tem prioridade. Sem caminho, a app escolhe a ROI com
  modificação mais recente na pasta anterior ou em `density_addendum_*/mntb_roi.tif`.
  O arquivo selecionado é informado em Activity. Para escolher outra, use Browse.
- `constant_xy` só se o mesmo contorno do Fiji foi aplicado ao stack inteiro.
- Volumes exibidos em µm³; densidade em núcleos por (100 µm)³, isto é, por
  1.000.000 µm³, não por 100 µm³.
- Espessura completa: `(last_tissue_z - first_tissue_z + 1) * z_spacing`.
- Correção nominal: densidade medida × espessura completa / espessura nominal.
  O fator usa os limites físicos antes das guardas. O volume corrigido é o
  volume efetivo × espessura nominal / espessura completa.
- Mantida a convenção M3: com guarda ativa, distância <= guarda é excluída;
  guarda de 0 µm ainda exclui o plano da superfície ativa. `none` desativa a guarda.
- Retração: pressupõe escala uniforme em Z e área XY preservada. Superfícies
  inclinadas/desiguais podem invalidar uma única espessura global. A contagem é
  descritiva dos núcleos retidos, não um estimador estereológico sem viés.
- Filtros não comprovam identidade neuronal. A população depende dos marcadores
  e dos critérios escolhidos.

## Saídas e preservação dos resultados

Exportações completas usam novas pastas com timestamp e os arquivos conhecidos:
TIFFs de máscaras, CSVs de propriedades/filtros/resumos, ROI, configuração JSON,
figuras QC e PDF. O PDF conserva a estrutura do notebook; densidades corrigidas
estão nos CSVs/TXT de densidade. Os resumos contêm resultados medidos e corrigidos.
`density_only` salva um complemento com timestamp na pasta da análise anterior.
Alterações de ROI invalidam a aceitação. Resultados não são salvos apenas ao aceitar.

## Organização para desenvolvimento

- `core.py`: funções científicas e leitura de TIFF, extraídas da versão M3.
- `runtime.py`: uma sessão por análise, execução das etapas e ciclo de vida.
- `stages/`: scripts Python de leitura, medição, visualização, filtros e exportação.
  São executados em um namespace isolado da sessão para preservar a lógica e os
  callbacks existentes. Não há leitura/execução de `.ipynb` em tempo de execução.
- `ui.py`: janela de configuração, tabelas, atividade e processamento em background.
- `config.py`: presets e descoberta de ROI.
- `tests/`: testes de densidade, guardas, equivalência M3 e integração sintética.

Essa é uma extração conservadora para facilitar a migração; as etapas ainda
compartilham nomes de variáveis dentro de uma sessão. Uma refatoração futura pode
substituir isso por objetos de domínio sem alterar a interface científica.

## Referências técnicas

- Threads e atualização da interface: https://napari.org/stable/guides/threading.html
- Empacotamento: https://packaging.python.org/en/latest/guides/writing-pyproject-toml/
- PyTorch e hardware: https://pytorch.org/get-started/locally/

Consulte `VALIDATION_LATEST.md` para o escopo real dos testes desta distribuição.
