import { Component, type ReactNode } from 'react';

interface Props {
  /** Shown in place of the children after one of them throws while rendering. */
  fallback: ReactNode | ((reset: () => void) => ReactNode);
  children: ReactNode;
}

/** Keeps a part of the screen that fails to draw from blanking the whole app. */
export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  reset = () => this.setState({ failed: false });

  render() {
    if (!this.state.failed) return this.props.children;
    const { fallback } = this.props;
    return typeof fallback === 'function' ? fallback(this.reset) : fallback;
  }
}
