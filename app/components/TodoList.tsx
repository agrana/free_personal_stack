'use client';

import { useState } from 'react';
import { createClient } from '@/app/lib/supabase';
import { Todo } from '@/app/types';

interface TodoListProps {
  todos: Todo[];
}

export default function TodoList({ todos: initialTodos }: TodoListProps) {
  const [todos, setTodos] = useState<Todo[]>(initialTodos);
  const [loading, setLoading] = useState<string | null>(null);
  const supabase = createClient();

  const toggleTodo = async (id: string, completed: boolean) => {
    setLoading(id);
    try {
      const { error } = await supabase
        .from('todos')
        .update({ completed: !completed, updated_at: new Date().toISOString() })
        .eq('id', id);

      if (error) throw error;

      setTodos(
        todos.map(todo =>
          todo.id === id
            ? {
                ...todo,
                completed: !completed,
                updated_at: new Date().toISOString(),
              }
            : todo
        )
      );
    } catch (error) {
      console.error('Error updating todo:', error);
    } finally {
      setLoading(null);
    }
  };

  const deleteTodo = async (id: string) => {
    setLoading(id);
    try {
      const { error } = await supabase.from('todos').delete().eq('id', id);

      if (error) throw error;

      setTodos(todos.filter(todo => todo.id !== id));
    } catch (error) {
      console.error('Error deleting todo:', error);
    } finally {
      setLoading(null);
    }
  };

  const addTodo = (newTodo: Todo) => {
    setTodos([newTodo, ...todos]);
  };

  if (todos.length === 0) {
    return (
      <div className='text-center py-8'>
        <p className='text-gray-500'>
          No todos yet. Add one above to get started!
        </p>
      </div>
    );
  }

  return (
    <div className='space-y-2'>
      {todos.map(todo => (
        <div
          key={todo.id}
          className={`flex items-center justify-between p-3 border rounded-lg ${
            todo.completed
              ? 'bg-gray-50 border-gray-200'
              : 'bg-white border-gray-300'
          }`}
        >
          <div className='flex items-center space-x-3'>
            <button
              onClick={() => toggleTodo(todo.id, todo.completed)}
              disabled={loading === todo.id}
              className={`w-5 h-5 rounded border-2 flex items-center justify-center ${
                todo.completed
                  ? 'bg-green-500 border-green-500 text-white'
                  : 'border-gray-300 hover:border-green-500'
              } ${loading === todo.id ? 'opacity-50' : ''}`}
            >
              {todo.completed && (
                <svg
                  className='w-3 h-3'
                  fill='currentColor'
                  viewBox='0 0 20 20'
                >
                  <path
                    fillRule='evenodd'
                    d='M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z'
                    clipRule='evenodd'
                  />
                </svg>
              )}
            </button>
            <span
              className={`${todo.completed ? 'line-through text-gray-500' : 'text-gray-900'}`}
            >
              {todo.title}
            </span>
          </div>
          <button
            onClick={() => deleteTodo(todo.id)}
            disabled={loading === todo.id}
            className='text-red-600 hover:text-red-800 disabled:opacity-50'
          >
            {loading === todo.id ? (
              <div className='w-4 h-4 border-2 border-red-600 border-t-transparent rounded-full animate-spin' />
            ) : (
              <svg
                className='w-4 h-4'
                fill='none'
                stroke='currentColor'
                viewBox='0 0 24 24'
              >
                <path
                  strokeLinecap='round'
                  strokeLinejoin='round'
                  strokeWidth={2}
                  d='M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16'
                />
              </svg>
            )}
          </button>
        </div>
      ))}
    </div>
  );
}
