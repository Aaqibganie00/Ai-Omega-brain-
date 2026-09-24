export type ToolDefinition = {
  id: 'file_read' | 'file_write' | 'directory_list' | 'project_inspect' | 'test' | 'build' | 'git'
  description: string
  enabled: false
  execute: never
}

export class ToolRegistry {
  private readonly definitions = new Map<string, ToolDefinition>()

  register(definition: ToolDefinition): this {
    this.definitions.set(definition.id, definition)
    return this
  }

  list(): ToolDefinition[] {
    return Array.from(this.definitions.values())
  }
}

export function createDefaultToolRegistry(): ToolRegistry {
  const registry = new ToolRegistry()
  const tools: Array<[ToolDefinition['id'], string]> = [
    ['file_read', 'Read a project file through a safe metadata-only interface.'],
    ['file_write', 'Write a project file through a safe metadata-only interface.'],
    ['directory_list', 'List directories through a safe metadata-only interface.'],
    ['project_inspect', 'Inspect project metadata through a safe metadata-only interface.'],
    ['test', 'Run tests only through a metadata-only interface.'],
    ['build', 'Run builds only through a metadata-only interface.'],
    ['git', 'Git operations are disabled in this foundation runtime.'],
  ]

  for (const [id, description] of tools) {
    registry.register({ id, description, enabled: false, execute: undefined as never })
  }

  return registry
}
