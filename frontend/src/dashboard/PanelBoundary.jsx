import { Component } from 'react';

// Error boundary: if one panel crashes while rendering, show that panel's error in its place
// instead of letting React unmount the whole dashboard. The error is shown and logged, never
// hidden. It clears on Retry or when `resetKey` changes (a new run).
// React only supports error boundaries as class components (no hook equivalent).
//
// variant="banner": the alarm banner failed, so the alarm state is UNKNOWN. Say so loudly;
// a missing banner must never read as "no alarm".

export default class PanelBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error(`[720] ${this.props.name} crashed`, error, info?.componentStack);
  }

  componentDidUpdate(prev) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const { name, variant, className = '' } = this.props;
    const retry = <button type="button" className="ack-btn" onClick={() => this.setState({ error: null })}>Retry</button>;

    if (variant === 'banner') {
      return (
        <div className="banner banner-crash" role="alert">
          <span className="banner-kind">ALARM STATE UNAVAILABLE</span>
          <span>The alarm banner could not render ({error.message}). Do not assume NORMAL; check the decision panel.</span>
          {retry}
        </div>
      );
    }
    if (variant === 'inline') {
      return <span className="inline-crash mono" role="alert" title={error.message}>{name} unavailable</span>;
    }
    return (
      <section className={`panel panel-crash ${className}`} role="alert">
        <h2 className="panel-label">{name.toUpperCase()}</h2>
        <p>This panel could not render: <span className="mono">{error.message}</span></p>
        <p className="panel-note">The rest of the dashboard is unaffected. Details are in the browser console.</p>
        {retry}
      </section>
    );
  }
}
