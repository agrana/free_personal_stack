'use client';

import { useState } from 'react';
import { createClient } from '@/app/lib/supabase';
import { Todo } from '@/app/types';

export default function AddTodo() {
  const [title, setTitle] = useState('');
  const [loading, setLoading] = useState(false);
  const supabase = createClient();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;

    setLoading(true);
    try {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) throw new Error('No user found');

      const { data, error } = await supabase
        .from('todos')
        .insert({
          title: title.trim(),
          user_id: user.id,
          completed: false,
        })
        .select()
        .single();

      if (error) throw error;

      // Reset form
      setTitle('');

      // Refresh the page to show the new todo
      window.location.reload();
    } catch (error) {
      console.error('Error adding todo:', error);
      alert('Failed to add todo. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className='flex space-x-2'>
      <input
        type='text'
        value={title}
        onChange={e => setTitle(e.target.value)}
        placeholder='Add a new todo...'
        className='flex-1 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        disabled={loading}
      />
      <button
        type='submit'
        disabled={loading || !title.trim()}
        className='px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed'
      >
        {loading ? (
          <div className='w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin' />
        ) : (
          'Add'
        )}
      </button>
    </form>
  );
}
