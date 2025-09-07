import { createServerClient } from '@/app/lib/supabase-server';
import { redirect } from 'next/navigation';
import TodoList from '@/app/components/TodoList';
import AddTodo from '@/app/components/AddTodo';

export default async function Home() {
  const supabase = createServerClient();

  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    redirect('/auth/signin');
  }

  // Fetch todos for the current user
  const { data: todos, error } = await supabase
    .from('todos')
    .select('*')
    .eq('user_id', session.user.id)
    .order('created_at', { ascending: false });

  if (error) {
    console.error('Error fetching todos:', error);
  }

  return (
    <div className='max-w-4xl mx-auto py-8 px-4 sm:px-6 lg:px-8'>
      <div className='bg-white rounded-lg shadow-sm border p-6'>
        <div className='mb-8'>
          <h1 className='text-3xl font-bold text-gray-900 mb-2'>
            Welcome to Your Personal Project!
          </h1>
          <p className='text-gray-600'>
            This is a minimal Next.js app with Supabase integration. It
            demonstrates authentication, database operations, and real-time
            updates.
          </p>
        </div>

        <div className='mb-8'>
          <h2 className='text-xl font-semibold text-gray-900 mb-4'>
            Todo List Demo
          </h2>
          <p className='text-gray-600 mb-4'>
            This todo list demonstrates Supabase database operations:
          </p>
          <ul className='list-disc list-inside text-gray-600 mb-4'>
            <li>Creating new todos</li>
            <li>Marking todos as complete</li>
            <li>Deleting todos</li>
            <li>Real-time updates (if enabled)</li>
          </ul>
        </div>

        <div className='space-y-6'>
          <AddTodo />
          <TodoList todos={todos || []} />
        </div>

        <div className='mt-8 p-4 bg-blue-50 rounded-lg'>
          <h3 className='text-lg font-semibold text-blue-900 mb-2'>
            🚀 What&apos;s Next?
          </h3>
          <ul className='text-blue-800 space-y-1'>
            <li>• Customize this app for your project</li>
            <li>• Add more database tables and relationships</li>
            <li>• Implement real-time features</li>
            <li>• Add file uploads with Supabase Storage</li>
            <li>• Deploy to Vercel with your custom domain</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
