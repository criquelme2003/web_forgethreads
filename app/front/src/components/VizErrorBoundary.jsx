import { Component } from 'react';

/**
 * Si un gráfico falla al dibujarse, muestra el error en su lugar en vez de dejar la página en
 * blanco (un error de render sin capturar desmonta toda la app). Se reinicia al cambiar `resetKey`.
 */
export default class VizErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('Error al dibujar la visualización:', error, info.componentStack);
  }

  componentDidUpdate(prev) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return (
        <div className="viz-error validation">
          <p className="status err" style={{ margin: 0 }}>No se pudo dibujar esta visualización.</p>
          <p className="small muted" style={{ margin: '6px 0 0' }}>{String(this.state.error.message || this.state.error)}</p>
        </div>
      );
    }
    return this.props.children;
  }
}
