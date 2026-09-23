import prisma from '../config/database.js';
import { supabase } from '../config/supabase.js';

const BATCH_SIZE = 20;
const MAX_RETRIES = 3;

export const processCleanup = async () => {
	try {
		const jobs = await prisma.outboxFileDelete.findMany({
			where: {
				OR: [
					{ status: 'PENDING' },
					{ status: 'FAILED', retryCount: { lt: MAX_RETRIES } },
				],
			},
			take: 100,
			orderBy: { createdAt: 'asc' },
		});

		if (jobs.length === 0) return;

		// Chia thành các batch nhỏ để tránh 1 lỗi làm hỏng toàn bộ
		for (let i = 0; i < jobs.length; i += BATCH_SIZE) {
			const batch = jobs.slice(i, i + BATCH_SIZE);
			const paths = batch.map((job) => job.storagePath);
			const jobIds = batch.map((job) => job.id);

			try {
				const { error } = await supabase.storage.from('BKMAP-images').remove(paths);

				if (error) {
					console.error(`[Worker] Batch ${i / BATCH_SIZE + 1} lỗi Supabase:`, error.message);
					await prisma.outboxFileDelete.updateMany({
						where: { id: { in: jobIds } },
						data: {
							status: 'FAILED',
							retryCount: { increment: 1 },
							errorMessage: error.message,
						},
					});
					continue;
				}

				// Batch thành công — xóa records
				await prisma.outboxFileDelete.deleteMany({
					where: { id: { in: jobIds } },
				});

				console.log(`[Worker] Dọn dẹp ${paths.length} files thành công.`);
			} catch (batchError) {
				console.error(`[Worker] Batch ${i / BATCH_SIZE + 1} exception:`, batchError.message);
				await prisma.outboxFileDelete.updateMany({
					where: { id: { in: jobIds } },
					data: {
						status: 'FAILED',
						retryCount: { increment: 1 },
						errorMessage: batchError.message,
					},
				}).catch(console.error);
			}
		}
	} catch (error) {
		console.error('[Worker] Lỗi hệ thống:', error);
	}
};
