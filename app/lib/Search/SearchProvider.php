<?php

declare(strict_types=1);

namespace OCA\ContentSearch\Search;

use OCP\DB\QueryBuilder\IQueryBuilder;
use OCP\Files\IRootFolder;
use OCP\IDBConnection;
use OCP\IURLGenerator;
use OCP\IUser;
use OCP\Search\IProvider;
use OCP\Search\ISearchQuery;
use OCP\Search\SearchResult;
use OCP\Search\SearchResultEntry;

class SearchProvider implements IProvider {
	private const SQL = <<<'SQL'
		WITH q AS (SELECT websearch_to_tsquery('dutch', ?) AS q),
		hit AS (
			SELECT c.fileid, c.content, ts_rank(c.tsv, q.q) AS rank
			FROM *PREFIX*contentsearch c, q
			WHERE c.tsv @@ q.q
			ORDER BY rank DESC, c.fileid
			LIMIT ? OFFSET ?
		)
		SELECT hit.fileid,
		       ts_headline('dutch', left(hit.content, 100000), q.q,
		                   'MaxWords=20, MinWords=8, StartSel=«, StopSel=»') AS snippet
		FROM hit, q
		ORDER BY hit.rank DESC, hit.fileid
		SQL;

	public function __construct(
		private IDBConnection $db,
		private IRootFolder $rootFolder,
		private IURLGenerator $url,
	) {
	}

	public function getId(): string {
		return 'contentsearch';
	}

	public function getName(): string {
		return 'Inhoud';
	}

	public function getOrder(string $route, array $routeParameters): int {
		return 10;
	}

	public function search(IUser $user, ISearchQuery $query): SearchResult {
		$term = trim($query->getTerm());
		if (mb_strlen($term) < 3) {
			return SearchResult::complete($this->getName(), []);
		}
		$limit = $query->getLimit();
		$offset = (int)($query->getCursor() ?? 0);

		$rows = $this->db->executeQuery(
			self::SQL,
			[$term, $limit, $offset],
			[IQueryBuilder::PARAM_STR, IQueryBuilder::PARAM_INT, IQueryBuilder::PARAM_INT],
		)->fetchAll();

		$userFolder = $this->rootFolder->getUserFolder($user->getUID());
		$entries = [];
		foreach ($rows as $row) {
			// Not owned by or shared with this user: skip
			$node = $userFolder->getFirstNodeById((int)$row['fileid']);
			if ($node === null) {
				continue;
			}
			$entries[] = new SearchResultEntry(
				$this->url->linkToRouteAbsolute('core.Preview.getPreviewByFileId', ['x' => 32, 'y' => 32, 'fileId' => $node->getId()]),
				$node->getName(),
				trim(preg_replace('/\s+/u', ' ', (string)$row['snippet'])),
				$this->url->linkToRouteAbsolute('files.View.showFile', ['fileid' => $node->getId()]),
				'icon-filetype-file',
			);
		}

		if (count($rows) < $limit) {
			return SearchResult::complete($this->getName(), $entries);
		}
		return SearchResult::paginated($this->getName(), $entries, $offset + $limit);
	}
}
