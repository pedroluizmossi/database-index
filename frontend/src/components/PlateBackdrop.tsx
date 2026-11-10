/**
 * Fundo da interface: a prancha "Latent Cartography" como um instrumento ligado. A tinta fica parada (a metade de
 * baixo levemente desfocada, para não brigar com tabelas), um feixe de varredura gira devagar dentro do disco e o
 * achado vermelhão pulsa. Tudo em CSS (`.plate*` em index.css), atrás do conteúdo e sem receber clique; com
 * movimento reduzido, fica parado.
 */
export function PlateBackdrop() {
  return (
    <div className="plate" aria-hidden="true">
      <div className="plate-ink" />
      <div className="plate-ink plate-ink--soft" />
      <div className="plate-sweep" />
      <div className="plate-pin" />
      <div className="plate-pin plate-pin--late" />
    </div>
  );
}
