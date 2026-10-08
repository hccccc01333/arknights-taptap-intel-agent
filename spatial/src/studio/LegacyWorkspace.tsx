import Workspace from '../Workspace';
import '../ui/dashboard.css';
import '../ui/v2.css';
import type { ComponentProps } from 'react';
export default function LegacyWorkspace(props:ComponentProps<typeof Workspace>){return <div className="legacy-pulse-shell"><Workspace {...props}/></div>;}
