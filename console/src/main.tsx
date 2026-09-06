import { render } from 'preact';
import { App } from './ui/App';
import './styles/tokens.css';
import './styles/app.css';

render(<App />, document.getElementById('root')!);
